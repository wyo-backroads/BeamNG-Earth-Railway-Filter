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
from types import ModuleType
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'resources')]
import build_release
import patch_server as patch

EARTH = Path(os.environ.get('BNE_FILTER_TEST_SOURCE', ROOT.parent / 'BeamNG.Earth-1.10.1-beta.dev.555'))
sys.path[:0] = [str(EARTH / 'server'), str(EARTH)]

SOURCE_FILE = EARTH / 'server/railways.py'
RUNTIME = (ROOT / 'resources/runtime.py').read_text(encoding='utf-8')
EXE = Path(os.environ.get('BNE_FILTER_TEST_EXE', ROOT.parent.parent / 'BeamNG.Earth-1.10.1-beta.dev.555.exe'))


def load_companion():
    script = build_release.main()
    spec = importlib.util.spec_from_file_location('rail_filter_companion', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, script


@unittest.skipUnless(SOURCE_FILE.is_file(), 'BeamNG.Earth source unavailable (BNE_FILTER_TEST_SOURCE)')
class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_FILE.read_bytes()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rail companion source ')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.target = self.folder / 'railways.py'
        self.target.write_bytes(self.source)
        self.data = self.folder / 'addon'
        self.data.mkdir()
        self.companion, built = load_companion()
        self.script = self.data / built.name
        shutil.copyfile(built, self.script)
        patch.atomic_json(self.data / 'installed.json', {'file': self.script.name})

    def test_native_manifest_options_are_valid(self):
        import addons

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
        self.assertEqual(self.target.read_bytes(), self.source)
        patch.apply_now(record)
        self.assertIn(patch.BEGIN, self.target.read_bytes())
        again = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        self.assertEqual(again['state'], 'installed')
        patch.atomic_json(self.data / 'options.json', {'serverAction': 'Restore original server', 'hideAbandonedRailways': False})
        record = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        patch.apply_now(record)
        self.assertEqual(self.target.read_bytes(), self.source)

    def test_upgrade_reuses_verified_original_instead_of_layering_patches(self):
        record = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        patch.apply_now(record)
        original_backup = Path(record['backup']).read_bytes()
        other_config = self.data / 'migrated-control.json'
        patch.atomic_json(other_config, {'requestToken': 'new', 'enabled': True, 'modPath': str(self.script)})
        upgraded = patch.prepare(self.target, other_config, self.data / 'upgrade.json', RUNTIME + '\n# upgrade\n', token='new')
        self.assertEqual(upgraded['originalSha'], patch.sha(self.source))
        self.assertEqual(Path(upgraded['backup']).read_bytes(), original_backup)
        patch.apply_now(upgraded)
        restored = patch.prepare(self.target, other_config, self.data / 'upgrade.json', RUNTIME, operation='restore', token='new')
        patch.apply_now(restored)
        self.assertEqual(self.target.read_bytes(), self.source)

    def test_uninstalled_or_invalid_companion_is_refused(self):
        (self.data / 'installed.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Add-ons menu'):
            self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        self.assertEqual(self.target.read_bytes(), self.source)

    def test_changed_server_is_not_overwritten(self):
        record = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        patch.apply_now(record)
        self.target.write_bytes(self.source + b'\n# upstream update\n')
        with self.assertRaisesRegex(RuntimeError, 'backup'):
            self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)

    def test_dev555_filter_reaches_native_track_index_and_tram_geometry(self):
        import fast
        import numpy as np
        import rails
        import railways

        if not hasattr(railways, '_rail_index') or getattr(fast._ef, 'VERSION', 0) < 491:
            self.skipTest('Earth source and earthfast with native railway index required')
        sys.path.insert(0, str(EARTH / 'server/tests'))
        self.addCleanup(sys.path.pop, 0)
        from test_roads import FRAME, features

        track = features(
            [[(0, y), (250, y)] for y in (10, 20, 30, 40, 50)],
            ['standard_gauge'] * 3 + ['tram'] * 2,
            ids=['active', 'disused', 'abandoned', 'old-tram', 'active-tram'],
            subtypes=['rail'] * 5,
            flags=[(3, 'is_abandoned'), (3, 'is_bridge', 0.4, 0.6)],
        )
        track.attrs['osm_railway'] = np.array(['rail', 'disused', 'abandoned', '', ''])
        abandoned_tram = features([[(0, 40), (250, 40)]], ['tram'], subtypes=['rail'],
                                  flags=[(0, 'is_abandoned')])
        box = (0., 0., 256., 256.)
        record = self.companion.setup(self.data, self.script, self.target, notify=False, defer=False)
        patch.apply_now(record)
        stock_tram_lines, stock_chunks = rails.tram_lines, rails.tile_chunks
        for hidden in (False, True):
            with self.subTest(hidden=hidden), mock.patch.dict(os.environ), mock.patch.object(
                    rails, 'tram_lines', stock_tram_lines), mock.patch.object(rails, 'tile_chunks', stock_chunks):
                for key in ('BNGE_PEERS', 'BNGE_WORLD_BUNDLE', 'BNGE_RAIL_FILTER_SNAPSHOT'):
                    os.environ.pop(key, None)
                patch.atomic_json(self.data / 'options.json', {'hideAbandonedRailways': hidden})
                mod = ModuleType('railways')
                exec(compile(self.target.read_bytes(), str(self.target), 'exec'), vars(mod))
                with mock.patch.dict(sys.modules, railways=mod):
                    self.assertEqual(mod.drawn(track, 0), ('standard_gauge', 0))
                    self.assertEqual(mod.drawn(track, 1), ('standard_gauge', 1))
                    self.assertEqual(mod.drawn(track, 2), None if hidden else ('standard_gauge', 2))
                    parts = mod.parts(FRAME, [track], box)
                    self.assertEqual({fid for fid, *_ in parts}, {'active', 'disused'} if hidden else
                                     {'active', 'disused', 'abandoned', 'old-tram'})
                    self.assertEqual(len(rails.tram_lines(FRAME, 0, 0, [track])), 1 if hidden else 3)
                    self.assertEqual(bool(rails.tile_chunks(FRAME, 0, 0, [abandoned_tram])), not hidden)
                    index = mod._rail_index(track, FRAME)
                    self.assertIs(mod._rail_index(track, FRAME), index)
                    if hidden:
                        track.attrs['osm_railway'] = np.array(['rail', 'disused', 'rail', '', ''])
                        self.assertEqual(mod.drawn(track, 2), ('standard_gauge', 0))
                        self.assertIsNot(mod._rail_index(track, FRAME), index)
        self.assertEqual(track.attrs['class'].tolist(), ['standard_gauge'] * 3 + ['tram'] * 2)


@unittest.skipUnless(EXE.is_file(), 'BeamNG.Earth Windows executable unavailable (BNE_FILTER_TEST_EXE)')
class ExecutableTests(unittest.TestCase):
    def test_signed_release_can_be_patched_and_restored_byte_for_byte(self):
        original = EXE.read_bytes()
        with tempfile.TemporaryDirectory(prefix='rail exe test ') as temporary:
            folder = Path(temporary)
            target = folder / EXE.name
            target.write_bytes(original)
            config = folder / 'control.json'
            status = folder / 'status.json'
            record = patch.prepare(target, config, status, RUNTIME)
            self.assertTrue(record['originalSigned'])
            patch.apply_now(record)
            self.assertNotEqual(patch.sha(target.read_bytes()), patch.sha(original))
            patch.apply_now(patch.prepare(target, config, status, RUNTIME, operation='restore'))
            self.assertEqual(target.read_bytes(), original)


@unittest.skipUnless(os.name == 'nt' and EXE.is_file() and SOURCE_FILE.is_file(),
                     'Windows and the BeamNG.Earth source and executable are required')
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
                    'local': {'file': str(built), 'tag': 'v0.3.2'}
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
 import mapgeom,numpy as np,railways,revisit
 f=mapgeom.build('roads',['example'],[(mapgeom.LINE,[[np.array([[0.,0.],[0.001,0.]])]])])
 f.attrs={'subtype':['rail'],'class':['standard_gauge'],'osm_railway':['abandoned']}
 f.rules={}
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
            import addons

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
