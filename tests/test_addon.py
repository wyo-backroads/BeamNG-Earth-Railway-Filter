import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'resources')]
import build_release
import patch_server as patch

EARTH = ROOT.parent / 'BeamNG.Earth-1.9.1-beta.dev.410'
sys.path[:0] = [str(EARTH / 'server'), str(EARTH)]
import addons

SOURCE = (EARTH / 'server/railways.py').read_bytes()
RUNTIME = (ROOT / 'resources/runtime.py').read_text(encoding='utf-8')
EXE = Path(os.environ.get('BNE_FILTER_TEST_EXE', ROOT.parent.parent / 'BeamNG.Earth-1.9.1-beta.dev.410.exe'))
if not EXE.is_file():
    EXE = ROOT.parent.parent / 'BeamNG.Earth-1.9.1-beta.dev.407.exe'


def load_companion():
    script = build_release.main()
    spec = importlib.util.spec_from_file_location('rail_filter_companion', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, script


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rail companion source ')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.target = self.folder / 'railways.py'
        self.target.write_bytes(SOURCE)
        self.data = self.folder / 'addon'
        self.data.mkdir()
        self.companion, built = load_companion()
        self.script = self.data / built.name
        shutil.copyfile(built, self.script)
        patch.atomic_json(self.data / 'installed.json', {'file': self.script.name})

    def test_native_manifest_options_are_valid(self):
        manifest = json.loads((ROOT / 'beamngEarth_addon.json').read_text())
        options = addons.parse_options(manifest['options'])
        self.assertEqual(options[0]['key'], 'hideAbandonedRailways')
        self.assertEqual(options[1]['choices'], ['Filter railways', 'Restore original server'])

    def test_certificate_alignment_padding_is_accepted_but_unknown_data_is_rejected(self):
        data = bytearray(528)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 128)
        data[128:132] = b'PE\0\0'
        struct.pack_into('<H', data, 152, 0x20b)
        struct.pack_into('<II', data, 152 + 112 + 32, 520, 8)
        header, signed, _ = patch.strip_signature(data, 514)
        self.assertTrue(signed)
        self.assertEqual(len(header), 514)
        data[515] = 1
        with self.assertRaises(patch.PatchError):
            patch.strip_signature(data, 514)
        data[515] = 0
        struct.pack_into('<II', data, 152 + 112 + 32, 521, 7)
        with self.assertRaises(patch.PatchError):
            patch.strip_signature(data, 514)

    def test_menu_setup_is_reversible_without_external_commands(self):
        record = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        self.assertEqual(record['state'], 'pending')
        self.assertEqual(self.target.read_bytes(), SOURCE)
        patch.apply_now(record)
        self.assertIn(patch.BEGIN, self.target.read_bytes())
        again = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        self.assertEqual(again['state'], 'installed')
        patch.atomic_json(self.data / 'options.json', {'serverAction': 'Restore original server', 'hideAbandonedRailways': False})
        record = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        patch.apply_now(record)
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_upgrade_reuses_verified_original_instead_of_layering_patches(self):
        record = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        patch.apply_now(record)
        original_backup = Path(record['backup']).read_bytes()
        other_config = self.data / 'migrated-control.json'
        patch.atomic_json(other_config, {'requestToken': 'new', 'enabled': True, 'modPath': str(self.script)})
        upgraded = patch.prepare(self.target, other_config, self.data / 'upgrade.json', RUNTIME + '\n# upgrade\n', token='new')
        self.assertEqual(upgraded['originalSha'], patch.sha(SOURCE))
        self.assertEqual(Path(upgraded['backup']).read_bytes(), original_backup)
        patch.apply_now(upgraded)
        restored = patch.prepare(self.target, other_config, self.data / 'upgrade.json', RUNTIME, operation='restore', token='new')
        patch.apply_now(restored)
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_uninstalled_or_invalid_companion_is_refused(self):
        (self.data / 'installed.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Add-ons menu'):
            self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_changed_server_is_not_overwritten(self):
        record = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        patch.apply_now(record)
        self.target.write_bytes(SOURCE + b'\n# upstream update\n')
        with self.assertRaisesRegex(RuntimeError, 'backup'):
            self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)


@unittest.skipUnless(os.name == 'nt' and EXE.is_file(), 'Windows packaged executable unavailable')
class MenuRunnerTests(unittest.TestCase):
    def test_real_native_install_start_options_workers_and_restore(self):
        built = build_release.main()
        original_sha = patch.sha(EXE.read_bytes())
        manifest = json.loads((ROOT / 'beamngEarth_addon.json').read_text())
        with tempfile.TemporaryDirectory(prefix='rail menu test ') as temporary:
            folder = Path(temporary)
            target = folder / 'BeamNG.Earth-test.exe'
            shutil.copyfile(EXE, target)
            spec = {
                'format': 1, 'addons': [{
                    'id': 'rail-menu-test', 'name': 'Railway test', 'author': 'Test fixture',
                    'repo': 'example/rail-menu-test', 'kind': 'companion',
                    'summary': 'Local test fixture', 'licence': 'MIT', 'asset': r'^earthrailfilter\.py$',
                    'run': manifest['run'], 'options': manifest['options'],
                    'local': {'file': str(built), 'tag': 'v0.3.0'}
                }]
            }
            patch.atomic_json(folder / 'registry.json', spec)
            harness = folder / 'native_menu.py'
            harness.write_text('''
from pathlib import Path
import json,time
import addons
root=Path(__file__).parent
registry=addons.parse_registry(json.loads((root/'registry.json').read_text()),local=True)
service=addons.Addons(str(root/'game'),str(root/'cache'),None,registry=registry,data_dir=str(root/'data'))
class Offline:
 def get(self,*a,**k):raise addons.AddonError('offline fixture')
service.github=Offline()
aid='rail-menu-test'
if not service.runner.installed(aid):service.install_companion(aid,consent=True)
service.run_op(aid,'start')
deadline=time.monotonic()+60
while service.runner.running(aid) and time.monotonic()<deadline:time.sleep(.1)
info=service.runner.info(aid)
status=root/'data/addons/rail-menu-test/patch-status.json'
(root/'ready.json').write_text(json.dumps({'info':info,'status':json.loads(status.read_text()) if status.exists() else {},'log':service.runner.log_tail(aid)}))
while not (root/'close.txt').exists():time.sleep(.1)
service.runner.stop_all()
''')
            data = folder / 'data/addons/rail-menu-test'
            status = data / 'patch-status.json'

            def run_menu_action():
                for name in ('ready.json', 'close.txt'):
                    path = folder / name
                    if path.exists():path.unlink()
                env = dict(os.environ, BNE_RAIL_FILTER_TEST_NO_PROMPT='1')
                process = subprocess.Popen([str(target), '--run-addon', str(harness)], env=env,
                                           creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic() + 70
                    while not (folder / 'ready.json').exists() and time.monotonic() < deadline:
                        self.assertIsNone(process.poll(), 'native menu harness exited early')
                        time.sleep(.2)
                    self.assertTrue((folder / 'ready.json').exists(), 'native Start did not finish')
                    ready = patch.read_json(folder / 'ready.json')
                    self.assertEqual(ready['status'].get('state'), 'pending', ready)
                    self.assertFalse(ready['info']['running'])
                    self.assertIn('normal launcher', '\n'.join(ready['log']))
                    self.assertTrue(any(p.name.startswith(target.name + '.earthRailFilter.') for p in folder.iterdir()))
                finally:
                    (folder / 'close.txt').write_text('close')
                    try: process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=0x08000000)
                    time.sleep(.5)
                deadline=time.monotonic()+35
                while time.monotonic()<deadline:
                    value=patch.read_json(status)
                    if value.get('state') != 'pending':return value
                    time.sleep(.2)
                self.fail('deferred replacement never finished')

            installed = run_menu_action()
            self.assertEqual(installed['state'], 'installed', installed)
            self.assertNotEqual(patch.sha(target.read_bytes()), original_sha)
            probe = folder / 'probe.py'
            probe.write_text('''
def run():
 import railways,revisit
 from types import SimpleNamespace
 f=SimpleNamespace(attrs={'subtype':['rail'],'class':['standard_gauge'],'osm_railway':['abandoned']},rules={})
 return {'result':railways.drawn(f,0),'cache':revisit.code_key(),'plans':revisit.switches_key(),'state':railways._earth_rail_filter}
''')
            check = folder / 'check.py'
            check.write_text('''
import json,multiprocessing,probe
from concurrent.futures import ProcessPoolExecutor
with ProcessPoolExecutor(1,mp_context=multiprocessing.get_context('spawn')) as p:
 child=p.submit(probe.run).result(timeout=30)
print('RESULT='+json.dumps({'main':probe.run(),'child':child}))
''')
            results=[]
            # Save via the real Addons options service, without starting the
            # setup companion again. Runtime reads those native saved options.
            entries=addons.parse_registry(spec,local=True)
            service=addons.Addons(str(folder/'game'),str(folder/'cache'),None,registry=entries,data_dir=str(folder/'data'))
            for hidden in (True,False):
                service.set_options('rail-menu-test',{'hideAbandonedRailways':hidden})
                result=subprocess.run([str(target),'--run-addon',str(check)],capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                value=json.loads(next(s for s in result.stdout.splitlines() if s.startswith('RESULT=')).split('=',1)[1])
                self.assertEqual(value['main']['result'],None if hidden else ['standard_gauge',2])
                self.assertEqual(value['child']['result'],value['main']['result'])
                results.append(value)
            self.assertNotEqual(results[0]['main']['cache'],results[1]['main']['cache'])
            self.assertNotEqual(results[0]['main']['plans'],results[1]['main']['plans'])
            service.set_options('rail-menu-test',{'hideAbandonedRailways':True})
            installed_script=data/'earthrailfilter.py'
            moved_script=data/'earthrailfilter.py.removed-test'
            installed_script.rename(moved_script)
            try:
                result=subprocess.run([str(target),'--run-addon',str(check)],capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                value=json.loads(next(s for s in result.stdout.splitlines() if s.startswith('RESULT=')).split('=',1)[1])
                self.assertEqual(value['main']['result'],['standard_gauge',2])
                self.assertEqual(value['child']['result'],['standard_gauge',2])
            finally:
                moved_script.rename(installed_script)
            service.set_options('rail-menu-test',{'serverAction':'Restore original server'})
            restored=run_menu_action()
            self.assertEqual(restored['state'],'restored',restored)
            self.assertEqual(patch.sha(target.read_bytes()),original_sha)
            self.assertEqual(patch.sha(EXE.read_bytes()),original_sha)


if __name__ == '__main__':
    unittest.main()
