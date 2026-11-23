"""Collect actual Cargo dependency notices for the Rust/Tauri distribution.

Build-only Python helper. It does not license Riot material or project code.
"""
import json
import base64
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]


def cargo_path():
    found = shutil.which('cargo')
    if found:
        return found
    candidate = Path.home() / '.cargo/bin/cargo.exe'
    if candidate.exists():
        return str(candidate)
    raise RuntimeError('Install the Rust toolchain before collecting Cargo notices.')

def dependency_graph(target, roots):
    metadata = json.loads(subprocess.check_output(
        [cargo_path(), 'metadata', '--format-version', '1', '--locked', '--all-features', '--filter-platform', target],
        cwd=ROOT, text=True, encoding='utf-8'))
    packages = {package['id']: package for package in metadata['packages']}
    nodes = {node['id']: node for node in metadata['resolve']['nodes']}
    pending = [identifier for identifier in metadata['workspace_members'] if packages[identifier]['name'] in roots]
    reachable = set()
    while pending:
        identifier = pending.pop()
        if identifier in reachable:
            continue
        reachable.add(identifier)
        pending.extend(dependency['pkg'] for dependency in nodes.get(identifier, {}).get('deps', []))
    return metadata, reachable

def upstream_license(repository, expression):
    parsed = urlparse(repository or '')
    parts = parsed.path.strip('/').removesuffix('.git').split('/')
    if parsed.hostname != 'github.com' or len(parts) < 2:
        return None
    owner, repo = parts[:2]
    cache = ROOT / 'target/license-texts' / f'{owner}_{repo}.json'
    if cache.exists():
        document = json.loads(cache.read_text(encoding='utf-8'))
    else:
        request = Request(f'https://api.github.com/repos/{owner}/{repo}/license',
                          headers={'Accept':'application/vnd.github+json','User-Agent':'prokjs-tft-license-build'})
        try:
            with urlopen(request, timeout=30) as response:
                document = json.load(response)
        except HTTPError as error:
            if error.code != 404:
                raise
            identifier = 'MIT' if ' OR ' in expression and 'MIT' in expression.split(' OR ') else expression
            if identifier not in {'MIT', 'Apache-2.0', 'MPL-2.0', 'BSD-3-Clause', 'BSL-1.0'}:
                raise
            source = f'https://raw.githubusercontent.com/spdx/license-list-data/main/text/{identifier}.txt'
            with urlopen(source, timeout=30) as response:
                text = response.read().decode('utf-8')
            document = {'encoding':'base64', 'content':base64.b64encode(text.encode()).decode(),
                        'html_url':source, 'source_kind':'SPDX standard terms for the crate-declared license'}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(document), encoding='utf-8')
    if document.get('encoding') != 'base64' or not document.get('content'):
        return None
    return document.get('html_url', repository), base64.b64decode(document['content']).decode('utf-8')




def main():
    metadata, native = dependency_graph('x86_64-pc-windows-msvc', {'tft-desktop', 'tft-core'})
    _, browser = dependency_graph('wasm32-unknown-unknown', {'tft-core'})
    members = set(metadata['workspace_members'])
    used = (native | browser) - members
    sections = [
        "THIRD-PARTY NOTICES — prokjs's Set 19 TFT tool (Rust/Tauri)",
        "Developed by prokjs. This file records dependency notices, not permission to redistribute Riot game content or third-party source material. The prototype remains subject to LEGAL.md and Riot review.",
        "Champion, trait and item content/artwork © Riot Games. CommunityDragon asset provenance: assets/manifest.json and data/item_icons.json. Game-data/build provenance: data/set19.json, data/item_builds.json, data/locales_game/provenance.json and data/team_planner_ids.json. Public URLs and source credits do not grant redistribution rights.",
        "The desktop runtime uses Tauri, Rust and the Windows-provided WebView2 Runtime. WebView2/Windows system libraries are not bundled by this application. The browser uses the same Rust engine compiled to WebAssembly. No Python interpreter or Pyodide runtime is included in this distribution.",
    ]
    missing = []
    for package in sorted(metadata['packages'], key=lambda row:(row['name'],row['version'])):
        if package['id'] not in used:
            continue
        directory = Path(package['manifest_path']).parent
        files = []
        explicit = package.get('license_file')
        if explicit and (directory / explicit).is_file():
            files.append(directory / explicit)
        for pattern in ('LICENSE*', 'LICENCE*', 'COPYING*', 'NOTICE*', 'COPYRIGHT*', 'license*', 'licenses/*'):
            files.extend(path for path in directory.glob(pattern) if path.is_file())
        files = sorted(set(files))
        title = f"{package['name']} {package['version']} — {package.get('license') or 'see license material'}"
        parts = [title, 'Source: ' + str(package.get('repository') or package.get('homepage') or package.get('source') or ''),
                 'Published authors: ' + '; '.join(package.get('authors', []))]
        if not files:
            upstream = upstream_license(package.get('repository'), package.get('license') or '')
            if upstream:
                parts.extend(['Upstream license: ' + upstream[0], upstream[1]])
            else:
                missing.append(package['name'])
                parts.append('The published crate does not include a license file; consult its SPDX expression and published source.')
        for path in files:
            parts.extend([path.name, path.read_text(encoding='utf-8', errors='replace')])
        sections.append('\n\n'.join(parts))
    rustc = Path(cargo_path()).with_name('rustc.exe' if Path(cargo_path()).suffix else 'rustc')
    sysroot = Path(subprocess.check_output([str(rustc), '--print', 'sysroot'], text=True, encoding='utf-8').strip())
    rust_licenses = list((sysroot / 'share/doc/rust').glob('LICENSE*')) + list((sysroot / 'share/doc/rust').glob('COPYRIGHT*'))
    for path in sorted(rust_licenses):
        if path.is_file():
            sections.append('Rust toolchain / standard library — ' + path.name + '\n\n' + path.read_text(encoding='utf-8',errors='replace'))
    destination = ROOT / 'THIRD_PARTY_NOTICES.txt'
    destination.write_text(('\n\n' + '='*72 + '\n\n').join(sections) + '\n', encoding='utf-8')
    print(f'Collected Cargo notices for {len(used)} native/browser dependency packages.')
    if missing:
        print('Published crates without top-level license text:', ', '.join(missing))


if __name__ == '__main__':
    main()
