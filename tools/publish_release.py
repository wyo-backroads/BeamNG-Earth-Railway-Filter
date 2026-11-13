"""Publish the built companion using this repository's temporary Actions token."""
import ast
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def request(url, method='GET', body=None, content_type='application/vnd.github+json'):
    headers = {
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': content_type,
        'User-Agent': 'earth-railway-filter-release'
    }
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode('utf-8')
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method=method), timeout=60) as response:
        return json.load(response)


def main():
    script = ROOT / 'dist/earthrailfilter.py'
    tree = ast.parse(script.read_text(encoding='utf-8'))
    version = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets))
    tag = 'v' + version
    repository = os.environ['GITHUB_REPOSITORY']
    base = 'https://api.github.com/repos/' + repository
    try:
        release = request(base + '/releases/tags/' + urllib.parse.quote(tag))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        release = request(base + '/releases', method='POST', body={
            'tag_name': tag, 'target_commitish': os.environ['GITHUB_SHA'],
            'name': 'Earth Railway Filter ' + version,
            'body': (ROOT / 'RELEASE_NOTES.md').read_text(encoding='utf-8'),
            'draft': False, 'prerelease': False, 'make_latest': 'true'
        })
    payload = script.read_bytes()
    digest = 'sha256:' + hashlib.sha256(payload).hexdigest()
    existing = next((asset for asset in release.get('assets', []) if asset['name'] == script.name), None)
    if existing:
        if existing.get('digest') != digest:
            raise RuntimeError('This version already has a different asset. Bump VERSION instead of replacing a published file.')
        asset = existing
    else:
        upload = release['upload_url'].split('{', 1)[0] + '?name=' + urllib.parse.quote(script.name)
        asset = request(upload, method='POST', body=payload, content_type='application/octet-stream')
        if asset.get('digest') != digest:
            raise RuntimeError('GitHub asset checksum did not match the built file')
    print('Release:', release['html_url'])
    print('Download:', asset['browser_download_url'])
    print('SHA-256:', digest)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as output:
            output.write(f"Published [{tag}]({release['html_url']}) with `{script.name}`.\n")


if __name__ == '__main__':
    main()
