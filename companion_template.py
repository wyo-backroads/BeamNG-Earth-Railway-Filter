"""Earth Railway Filter. Installed and started by BeamNG.Earth's own Add-ons menu.

Players do not execute or edit this file. The packaged app supplies its Python.
"""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

VERSION = '0.3.1'
PAYLOAD = '__HELPERS_ZIP_BASE64__'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {} if default is None else default


def unpack(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(PAYLOAD))) as archive:
        for name in archive.namelist():
            if name not in ('patch_server.py', 'runtime.py', 'deferred.ps1'):
                raise RuntimeError('Unsupported embedded helper resource')
            (folder / name).write_bytes(archive.read(name))
    spec = importlib.util.spec_from_file_location('_earth_rail_filter_patch', folder / 'patch_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_target():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve()
    # Source launchers already imported apppaths from their installation root.
    import apppaths
    root = Path(apppaths.__file__).resolve().parent
    if not (root / 'server/railways.py').is_file():
        raise RuntimeError('Could not find the source Earth server. Start this add-on from its normal Earth installation.')
    return root / 'server/railways.py'


def start_deferred(record, helper):
    command = "& '{}' -Receipt '{}'".format(str(helper).replace("'", "''"), record['status'].replace("'", "''"))
    encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                      '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                     creationflags=0x08000000 | 0x00000200, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def setup(folder, script, target, parent_pid=0, notify=True, defer=True):
    """The menu's Start action. Also used directly by integration tests."""
    folder, script, target = Path(folder).resolve(), Path(script).resolve(), Path(target).resolve()
    installed_path = folder / 'installed.json'
    installed = read_json(installed_path)
    if installed.get('file') != script.name or not script.is_file():
        raise RuntimeError('Install this add-on through the BeamNG.Earth Add-ons menu first, then click Start.')
    options_path = folder / 'options.json'
    values = read_json(options_path)
    action = values.get('serverAction', 'Filter railways')
    hidden = values.get('hideAbandonedRailways', True)
    if type(hidden) is not bool or action not in ('Filter railways', 'Restore original server'):
        raise RuntimeError('The saved options are invalid. Use Reset in the add-on Options tab, then Start again.')
    control_path, status_path = folder / 'control.json', folder / 'patch-status.json'
    control = {
        'enabled': action != 'Restore original server', 'modPath': str(script),
        'installedPath': str(installed_path), 'optionsPath': str(options_path),
        'requestToken': uuid.uuid4().hex
    }
    atomic_json(control_path, control)
    patcher = unpack(folder / '_patch')
    operation = 'restore' if action == 'Restore original server' else 'install'
    record = patcher.prepare(target, control_path, status_path,
                             (folder / '_patch/runtime.py').read_text(encoding='utf-8'),
                             operation=operation, token=control['requestToken'], server_pid=parent_pid)
    if record['state'] == 'pending':
        record.update(notify=bool(notify), companionPid=os.getpid() if defer else 0)
        atomic_json(record['sidecar'], record)
        atomic_json(status_path, record)
        if defer:
            start_deferred(record, folder / '_patch/deferred.ps1')
        print('Setup is ready. Restart BeamNG.Earth using its normal launcher.', flush=True)
    else:
        print(record['message'], flush=True)
    return record


def main():
    if os.name != 'nt':
        print('This test build supports Windows only.', file=sys.stderr)
        return 1
    try:
        script = Path(__file__).resolve()
        parent = int(os.environ.get('BNGEARTH_PARENT', '0'))
        if not parent:
            raise RuntimeError('Start this add-on with the Start button in BeamNG.Earth Add-ons. No commands or external installer are needed.')
        setup(script.parent, script, find_target(), parent_pid=parent,
              notify=os.environ.get('BNE_RAIL_FILTER_TEST_NO_PROMPT') != '1')
        return 0
    except Exception as error:
        try:
            atomic_json(Path(__file__).resolve().parent / 'patch-status.json',
                        {'state': 'error', 'message': str(error), 'version': VERSION})
        except OSError:
            pass
        print('Railway setup could not finish: ' + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
