import base64
import io
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent


def main():
    resources = io.BytesIO()
    with zipfile.ZipFile(resources, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ('patch_server.py', 'runtime.py', 'deferred.ps1'):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            content = (ROOT / 'resources' / name).read_text(encoding='utf-8')
            archive.writestr(info, content.encode('utf-8'))
    template = (ROOT / 'companion_template.py').read_text(encoding='utf-8')
    script = template.replace('__HELPERS_ZIP_BASE64__', base64.b64encode(resources.getvalue()).decode('ascii'))
    target = ROOT / 'dist/earthrailfilter.py'
    target.parent.mkdir(exist_ok=True)
    target.write_text(script, encoding='utf-8', newline='\n')
    print(target)
    return target


if __name__ == '__main__':
    main()
