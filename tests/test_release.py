import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_release

spec = importlib.util.spec_from_file_location('release_publisher', ROOT / 'tools/publish_release.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class ReleaseTests(unittest.TestCase):
    def test_build_is_reproducible(self):
        first = build_release.main().read_bytes()
        second = build_release.main().read_bytes()
        self.assertEqual(first, second)
        self.assertNotIn(b'\r\n', first)

    def test_release_comparison_accepts_line_endings_and_rejects_changed_code(self):
        built = build_release.main().read_bytes()
        self.assertEqual(publisher.code_identity(built), publisher.code_identity(built.replace(b'\n', b'\r\n')))
        changed = built.replace(b'def main():', b'def changed_main():', 1)
        self.assertNotEqual(publisher.code_identity(built), publisher.code_identity(changed))


if __name__ == '__main__':
    unittest.main()
