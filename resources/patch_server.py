"""Prepare a reversible, local railway-module patch. Uses only Python's standard library.

Frozen apps execute this through their existing --run-addon entry point so the
compiler/marshal version always matches the embedded interpreter. No alternate
launcher is installed. Replacement waits until the normal Earth app exits.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import types
import uuid
import zlib

VERSION = "0.3.1"
MARKER = "EARTH_RAIL_FILTER_0_3_0"
COOKIE = struct.Struct("!8sIIII64s")
ENTRY = struct.Struct("!IIIIBc")
MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
BEGIN = b"\n# BEGIN EARTH_RAIL_FILTER_0_3_0\n"
END = b"# END EARTH_RAIL_FILTER_0_3_0\n"


class PatchError(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def injection(runtime, config):
    # Execute the helper in its own namespace; leave Earth's module globals
    # (and functions used by other modules) intact except the explicit hook.
    return (f"\n# {MARKER}\n"
            f"_earth_rail_filter_patch = {MARKER!r}\n"
            "_erf_runtime = {}\n"
            f"exec({runtime!r}, _erf_runtime)\n"
            f"_erf_runtime['install'](globals(), {str(config)!r})\n"
            "del _erf_runtime\n")


def validate_module(code):
    if not isinstance(code, types.CodeType) or not {"drawn", "wear", "parts", "WEAR_FLAGS"} <= set(code.co_names):
        raise PatchError("Unsupported railway module; expected Earth's drawn/wear/parts API")
    functions = {c.co_name: c for c in code.co_consts if isinstance(c, types.CodeType)}
    drawn = functions.get("drawn")
    if drawn is None or drawn.co_varnames[:3] != ("feats", "i", "bridges"):
        raise PatchError("Unsupported railways.drawn signature; no files changed")
    # This format relies on wear 2 meaning abandoned. Reject other semantics.
    if not any(isinstance(value, tuple) and ("is_abandoned", 2) in value for value in code.co_consts):
        raise PatchError("The expected abandonment classification was not found")


def patch_source(data, runtime, config):
    if BEGIN in data or MARKER.encode() in data:
        raise PatchError("A patch is already present without a matching receipt")
    validate_module(compile(data, "railways.py", "exec"))
    return data + BEGIN + injection(runtime, config).encode("utf-8") + END


def patch_pyz(data, runtime, config):
    if data[:4] != b"PYZ\x00" or data[4:8] != importlib.util.MAGIC_NUMBER:
        raise PatchError("Embedded Python version does not match; run the patcher through this Earth's --run-addon")
    offset = struct.unpack_from("!i", data, 8)[0]
    if not 12 <= offset < len(data):
        raise PatchError("Invalid PYZ table offset")
    raw_toc = marshal.loads(data[offset:])
    toc = dict(raw_toc)
    if "railways" not in toc:
        raise PatchError("This executable does not contain Earth's railway module")
    kind, pos, length = toc["railways"]
    if kind != 0 or pos < 12 or pos + length > offset:
        raise PatchError("Unsupported railways archive entry")
    original = zlib.decompress(data[pos:pos + length])
    code = marshal.loads(original)
    validate_module(code)
    if MARKER in repr(code.co_consts):
        raise PatchError("A patch is already present without a matching receipt")
    # Preserve the original bytecode verbatim, and execute it before the hook.
    wrapped = ("import marshal as _erf_m, zlib as _erf_z, base64 as _erf_b\n"
               f"exec(_erf_m.loads(_erf_z.decompress(_erf_b.b64decode({base64.b64encode(zlib.compress(original)).decode()!r}))))\n"
               "del _erf_m, _erf_z, _erf_b\n" + injection(runtime, config))
    changed = zlib.compress(marshal.dumps(compile(wrapped, code.co_filename, "exec")), 6)
    # Append the replacement immediately before the TOC. Unchanged entries keep
    # their original offsets and compressed bytes; only this entry is redirected.
    toc["railways"] = (kind, offset, len(changed))
    result = bytearray(data[:offset] + changed)
    struct.pack_into("!i", result, 8, len(result))
    result.extend(marshal.dumps(list(toc.items()) if isinstance(raw_toc, list) else toc))
    return bytes(result)


def archive_entries(data):
    cookie_pos = data.rfind(MAGIC)
    if cookie_pos < 0 or cookie_pos + COOKIE.size > len(data):
        raise PatchError("No supported PyInstaller archive found")
    magic, size, toff, tlen, py, lib = COOKIE.unpack_from(data, cookie_pos)
    start = cookie_pos + COOKIE.size - size
    if start < 0 or toff < 0 or start + toff + tlen != cookie_pos:
        raise PatchError("Invalid archive boundaries")
    records, at, stop = [], start + toff, start + toff + tlen
    while at < stop:
        n, pos, length, raw, comp, kind = ENTRY.unpack_from(data, at)
        if n < ENTRY.size or at + n > stop or pos + length > toff or comp not in (0, 1):
            raise PatchError("Invalid archive table entry")
        name_bytes = data[at + ENTRY.size:at + n]
        name = name_bytes.rstrip(b"\0").decode("utf-8")
        records.append((n, pos, length, raw, comp, kind, name_bytes, name))
        at += n
    if at != stop:
        raise PatchError("Invalid archive table size")
    return start, toff, cookie_pos, py, lib, records


def strip_signature(data, cookie_end):
    """A modified app cannot retain its publisher's Authenticode signature."""
    if data[:2] != b"MZ":
        raise PatchError("Only Windows PE executables are supported by this installer")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise PatchError("Invalid PE executable")
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise PatchError("Unsupported PE optional header")
    directory = opt + (112 if magic == 0x20B else 96)
    cert, size = struct.unpack_from("<II", data, directory + 32)
    if size:
        # Authenticode aligns the certificate table to eight bytes. Some builds
        # (including dev.410) therefore have zero padding after the archive.
        # Accept only that exact alignment gap, never an arbitrary suffix.
        aligned_end = (cookie_end + 7) & ~7
        if cert != aligned_end or cert + size != len(data) or any(data[cookie_end:cert]):
            raise PatchError("Unsupported signed-app layout; original file left untouched")
    if not size and cookie_end != len(data):
        raise PatchError("Unexpected data after application archive")
    header = bytearray(data[:cookie_end])
    struct.pack_into("<II", header, directory + 32, 0, 0)
    struct.pack_into("<I", header, opt + 64, 0)  # PE checksum, recalculated below.
    return header, bool(size), opt + 64


def pe_checksum(data, checksum_at):
    import array
    value = 0
    for at in range(0, len(data), 1024 * 1024):
        chunk = bytes(data[at:at + 1024 * 1024])
        if len(chunk) % 2:
            chunk += b"\0"
        words = array.array("H")
        words.frombytes(chunk)
        if sys.byteorder != "little":
            words.byteswap()
        value += sum(words)
    value -= sum(struct.unpack_from("<HH", data, checksum_at))
    while value >> 16:
        value = (value & 0xFFFF) + (value >> 16)
    return value + len(data)


def patch_executable(data, runtime, config):
    start, toff, cookie_pos, py, lib, entries = archive_entries(data)
    if py != sys.version_info.major * 100 + sys.version_info.minor:
        raise PatchError("Use the target application's own Python to prepare this patch")
    candidates = [e for e in entries if e[5] == b"z"]
    if len(candidates) != 1:
        raise PatchError("Expected one embedded Python archive")
    selected = candidates[0]
    old_pos, old_len = selected[1:3]
    pz = data[start + old_pos:start + old_pos + old_len]
    if selected[4]:
        pz = zlib.decompress(pz)
    new_pz = patch_pyz(pz, runtime, config)
    raw_len = len(new_pz)
    if selected[4]:
        new_pz = zlib.compress(new_pz)
    delta = len(new_pz) - old_len
    header, signed, checksum_at = strip_signature(data, cookie_pos + COOKIE.size)
    body = bytes(header[start:start + old_pos]) + new_pz + bytes(header[start + old_pos + old_len:start + toff])
    toc = bytearray()
    for n, pos, length, raw, comp, kind, namebytes, name in entries:
        if name == selected[7]:
            length, raw = len(new_pz), raw_len
        elif pos >= old_pos + old_len:
            pos += delta
        elif length and old_pos < pos < old_pos + old_len:
            raise PatchError("Overlapping application archive entries")
        toc.extend(ENTRY.pack(n, pos, length, raw, comp, kind) + namebytes)
    cookie = COOKIE.pack(MAGIC, len(body) + len(toc) + COOKIE.size, len(body), len(toc), py, lib)
    result = bytearray(header[:start]) + body + toc + cookie
    struct.pack_into("<I", result, checksum_at, pe_checksum(result, checksum_at))
    archive_entries(result)  # Check the completed archive before any file write.
    return bytes(result), signed


def prepare(target, config, status, runtime, operation="install", token="", server_pid=0):
    target = Path(target).resolve(strict=True)
    if target.is_dir():
        target = target / "server/railways.py"
    if not target.is_file() or not (target.suffix.lower() == ".exe" or target.name == "railways.py"):
        raise PatchError("Select a BeamNG.Earth executable or its source folder")
    config, status = Path(config).resolve(), Path(status).resolve()
    sidecar = target.with_name(target.name + ".earthRailFilter.json")
    backup = target.with_name(target.name + ".earthRailFilter.original")
    before = target.read_bytes()
    previous = read_json(sidecar)
    digest = sha(before)
    original_sha = digest
    runtime_sha = sha(runtime.encode('utf-8'))
    if operation == "restore":
        if not previous:
            raise PatchError("No recorded patch for this server")
        if digest == previous["originalSha"]:
            record = dict(previous, state="restored", message="Original server is already present.", token=token)
            atomic_json(sidecar, record)
            atomic_json(status, record)
            return record
        if digest != previous["patchedSha"]:
            raise PatchError("Server changed after patching; restoration would overwrite an update")
        if not backup.is_file() or sha(backup.read_bytes()) != previous["originalSha"]:
            raise PatchError("Original backup is missing or has a different checksum")
        after = backup.read_bytes()
        signed = previous.get("originalSigned", False)
    else:
        pristine = before
        if previous and digest == previous.get("patchedSha"):
            if str(config) == previous.get("config") and previous.get('runtimeSha') == runtime_sha:
                record = dict(previous, state="installed", message="The railway filter is installed. Change the toggle in Options, then restart Earth.",
                              token=token, serverPid=server_pid)
                atomic_json(sidecar, record)
                atomic_json(status, record)
                return record
            if not backup.is_file() or sha(backup.read_bytes()) != previous.get('originalSha'):
                raise PatchError('The original backup could not be verified; existing patch was preserved')
            pristine = backup.read_bytes()
            original_sha = previous['originalSha']
        elif backup.exists() and (not previous or sha(backup.read_bytes()) != previous.get("originalSha") or digest != previous.get("originalSha")):
            raise PatchError("Existing backup belongs to a different server build; it was preserved")
        if target.suffix.lower() == ".exe":
            after, signed = patch_executable(pristine, runtime, config)
        else:
            after, signed = patch_source(pristine, runtime, config), False
        if not backup.exists():
            with backup.open("xb") as stream:
                stream.write(pristine)
        if sha(backup.read_bytes()) != original_sha:
            raise PatchError("Backup verification failed; original server not changed")
    pending = target.with_name(target.name + ".earthRailFilter." + uuid.uuid4().hex + ".pending")
    with pending.open("xb") as stream:
        stream.write(after)
    if sha(pending.read_bytes()) != sha(after):
        raise PatchError("Staged patch verification failed")
    if operation == "install":
        patched_sha = sha(after)
    else:
        original_sha, patched_sha = previous["originalSha"], previous["patchedSha"]
    record = {
        "version": VERSION, "state": "pending", "operation": operation,
        "target": str(target), "backup": str(backup), "pending": str(pending),
        "expectedSha": digest, "replacementSha": sha(after), "originalSha": original_sha,
        "patchedSha": patched_sha, "originalSigned": signed, "config": str(config),
        "runtimeSha": runtime_sha,
        "status": str(status), "sidecar": str(sidecar), "token": token,
        "serverPid": server_pid, "interpreter": sys.executable,
        "message": "Patch ready. Close BeamNG.Earth completely, then start it normally." if operation == "install"
                   else "Restore ready. Close BeamNG.Earth completely, then start it normally."
    }
    atomic_json(sidecar, record)
    atomic_json(status, record)
    return record


def apply_now(record):
    """For tests/offline CLI only. The in-game installer uses deferred.ps1."""
    target, pending = Path(record["target"]), Path(record["pending"])
    if sha(target.read_bytes()) != record["expectedSha"] or sha(pending.read_bytes()) != record["replacementSha"]:
        raise PatchError("Files changed after preparation; refusing to overwrite them")
    state = read_json(record["config"])
    if record["token"] and state.get("requestToken") != record["token"]:
        raise PatchError("A newer request superseded this patch")
    os.replace(pending, target)
    finished = dict(record, state="installed" if record["operation"] == "install" else "restored",
                    message="Local server patch installed." if record["operation"] == "install" else "Original server restored.")
    atomic_json(record["sidecar"], finished)
    atomic_json(record["status"], finished)
    return finished


def command_args(command):
    if os.name != "nt":
        import shlex
        return shlex.split(command)
    import ctypes
    from ctypes import wintypes
    count = ctypes.c_int()
    shell = ctypes.WinDLL("shell32")
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    values = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not values:
        raise PatchError("Could not parse the server command line")
    try:
        return [values[i] for i in range(count.value)]
    finally:
        kernel = ctypes.WinDLL("kernel32")
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel.LocalFree(ctypes.cast(values, wintypes.HLOCAL))


def job_target(job):
    if job.get("serverPath"):
        return Path(job["serverPath"])
    exe = Path(job.get("executable", ""))
    if exe.name.lower().startswith("beamng.earth") and exe.suffix.lower() == ".exe":
        return exe
    for argument in command_args(job.get("commandLine", "")):
        entry = Path(argument)
        if entry.name == "launcher.py" and (entry.parent / "server/railways.py").is_file():
            return entry.parent
        if entry.name == "earthd.py" and (entry.parent / "railways.py").is_file():
            return entry.parent / "railways.py"
    raise PatchError("Could not locate the Earth server. Enter its executable or source folder in the UI app.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--status", type=Path)
    parser.add_argument("--runtime", type=Path, default=Path(__file__).with_name("runtime.py"))
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--apply-now", action="store_true", help="offline copies only; target must not be running")
    args = parser.parse_args(argv)
    job = read_json(args.job) if args.job else {}
    status = args.status or (Path(job["statusPath"]) if job else None)
    try:
        target = args.target or job_target(job)
        config = args.config or Path(job["configPath"])
        if status is None:
            raise PatchError("--status or --job is required")
        runtime = args.runtime.read_text(encoding="utf-8")
        operation = "restore" if args.restore or job.get("operation") == "restore" else "install"
        record = prepare(target, config, status, runtime, operation, job.get("requestToken", ""), int(job.get("serverPid", 0)))
        if record["state"] == "pending":
            record["onlyIfInactive"] = job.get("onlyIfInactive") is True
            atomic_json(record["sidecar"], record)
            atomic_json(status, record)
            if args.apply_now:
                record = apply_now(record)
            elif args.job:
                # Only a short-lived installation helper; it exits after Earth
                # closes and atomically replaces the verified file.
                deferred = Path(__file__).with_name("deferred.ps1")
                script = f"& '{str(deferred).replace(chr(39), chr(39)*2)}' -Receipt '{str(status).replace(chr(39), chr(39)*2)}'"
                encoded = base64.b64encode(script.encode("utf-16le")).decode()
                subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                                  "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                                 creationflags=0x08000000, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(record["message"], flush=True)
        return 0
    except Exception as error:
        if status:
            atomic_json(status, {"state": "error", "message": str(error), "version": VERSION})
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
