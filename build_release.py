import base64
import io
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent


def main():
    resources = io.BytesIO()
    with zipfile.ZipFile(resources, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ('patch_server.py', 'runtime.py', 'deferred.ps1'):
            archive.write(ROOT / 'resources' / name, name)
    template = (ROOT / 'companion_template.py').read_text(encoding='utf-8')
    script = template.replace('__HELPERS_ZIP_BASE64__', base64.b64encode(resources.getvalue()).decode('ascii'))
    target = ROOT / 'dist/earthrailfilter.py'
    target.parent.mkdir(exist_ok=True)
    target.write_text(script, encoding='utf-8')
    print(target)
    return target


if __name__ == '__main__':
    main()
