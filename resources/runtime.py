"""Injected railway filter. The stock module executes before install() is called."""
VERSION = "0.3.2"


def install_classifier(namespace):
    import functools

    stock_columns = namespace.get("_drawn_cols")
    if stock_columns is not None:
        @functools.wraps(stock_columns)
        def drawn_columns(feats):
            columns = stock_columns(feats)
            cached = feats.__dict__.get("_earth_rail_filter_columns")
            if cached is not None and cached[0] is columns:
                return cached[1]
            plain, bridges, wear = columns
            # The native track index and drawn() both consume these class codes.
            # Preserve the stock columns and return a stable tuple for Earth's index cache.
            dropped = [i for i, value in enumerate(wear) if value == 2 and bridges[i] >= 0]
            filtered = columns
            if dropped:
                plain, bridges = plain.copy(), bridges.copy()
                for i in dropped:
                    plain[i] = bridges[i] = -1
                filtered = (plain, bridges, wear)
            feats.__dict__["_earth_rail_filter_columns"] = (columns, filtered)
            return filtered

        namespace["_drawn_cols"] = drawn_columns

        def abandoned_tram(feats, i):
            _, bridges, wear = stock_columns(feats)
            return bridges[i] >= 0 and wear[i] == 2

        return abandoned_tram

    stock_drawn = namespace["drawn"]

    def abandoned_tram(feats, i):
        result = stock_drawn(feats, i, bridges=True)
        return result is not None and result[1] == 2

    @functools.wraps(stock_drawn)
    def drawn(feats, i, bridges=False):
        result = stock_drawn(feats, i, bridges=bridges)
        return None if result is not None and result[1] == 2 else result

    namespace["drawn"] = drawn
    return abandoned_tram


def install(namespace, config_path):
    import copy
    import functools
    import json
    import logging
    import multiprocessing
    import os
    from pathlib import Path
    import tempfile
    import time

    logger = logging.getLogger("earthd.earthRailFilter")
    hidden = False
    reason = ""
    try:
        state = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
        proof = Path(state.get("modPath", ""))
        active = state.get("enabled") is True and proof.is_file()
        options = json.loads(Path(state['optionsPath']).read_text(encoding='utf-8-sig')) if Path(state['optionsPath']).is_file() else {}
        installed = json.loads(Path(state['installedPath']).read_text(encoding='utf-8-sig'))
        active = active and installed.get('file') == proof.name
        if active and state.get("modName"):
            dbpath = Path(state["userDir"]) / "mods/db.json"
            if dbpath.is_file():
                db = json.loads(dbpath.read_text(encoding="utf-8-sig"))
                entry = db.get("mods", {}).get(state["modName"], {})
                active = entry.get("active") is not False
        hidden = active and options.get('hideAbandonedRailways', True) is True and options.get('serverAction', 'Filter railways') != 'Restore original server'
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        logger.warning("Railway filter inactive: %s", error)
    if hidden:
        import sys
        peers = os.environ.get("BNGE_PEERS", "")
        for index, argument in enumerate(sys.argv):
            if argument == "--peers" and index + 1 < len(sys.argv):
                peers = sys.argv[index + 1]
            elif argument.startswith("--peers="):
                peers = argument.split("=", 1)[1]
        if peers.strip() or os.environ.get("BNGE_WORLD_BUNDLE"):
            hidden = False
            reason = "Filtering is inactive: disable legacy --peers helpers and pinned world bundles, then restart Earth."
            logger.warning(reason)

    snapshot_key = "BNGE_RAIL_FILTER_SNAPSHOT"
    child = multiprocessing.current_process().name != "MainProcess"
    if child and os.environ.get(snapshot_key):
        snapshot = json.loads(os.environ[snapshot_key])
        if snapshot.get("config") == config_path:
            hidden = snapshot["hidden"] is True
            reason = snapshot.get("reason", "")
    else:
        os.environ[snapshot_key] = json.dumps({"config": config_path, "hidden": hidden, "reason": reason})
    if hidden:
        # Earth includes BNE_* switches in mesh and terrain-plan cache keys.
        os.environ["BNE_ABANDONED_RAIL_FILTER"] = VERSION
    else:
        os.environ.pop("BNE_ABANDONED_RAIL_FILTER", None)

    namespace["_earth_rail_filter"] = {"version": VERSION, "hidden": hidden, "config": config_path, "reason": reason}
    if not child:
        try:
            status_path = Path(config_path).with_name("runtime-status.json")
            fd, temporary = tempfile.mkstemp(dir=status_path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(dict(namespace["_earth_rail_filter"], pid=os.getpid(), at=time.time()), stream)
            os.replace(temporary, status_path)
        except OSError as error:
            logger.warning("Could not write railway filter status: %s", error)
    if not hidden:
        return

    abandoned_tram = install_classifier(namespace)
    import rails
    import numpy as np

    def tram_cells(cells):
        for feats in cells:
            if feats is None or "class" not in feats.attrs or "subtype" not in feats.attrs:
                yield feats
                continue
            hit = np.flatnonzero((np.asarray(feats.attrs["class"]).astype(str) == "tram")
                                 & (np.asarray(feats.attrs["subtype"]).astype(str) == "rail"))
            dropped = [i for i in hit if abandoned_tram(feats, i)]
            if not dropped:
                yield feats
                continue
            view = copy.copy(feats)
            view.attrs = dict(feats.attrs)
            view.attrs["class"] = np.array(feats.attrs["class"], dtype="<U64")
            view.attrs["class"][dropped] = "unknown"
            yield view

    def wrap(fn):
        @functools.wraps(fn)
        def filtered(frame, tx, ty, cells, *args, **kwargs):
            return fn(frame, tx, ty, list(tram_cells(cells)), *args, **kwargs)
        return filtered

    rails.tile_chunks = wrap(rails.tile_chunks)
    rails.tram_lines = wrap(rails.tram_lines)
    logger.info("Abandoned railway filter active (%s)", VERSION)
