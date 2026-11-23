"""Generate redistribution notices for the actual desktop build dependencies.

No licence or commercial approval for Riot/third-party game content is implied.
"""
from importlib import metadata
from pathlib import Path
import json
import sys
import tkinter

ROOT = Path(__file__).resolve().parents[1]


def package_license(name):
    distribution = metadata.distribution(name)
    paths = [distribution.locate_file(file) for file in distribution.files
             if Path(str(file)).name.lower().startswith(('license', 'copying'))]
    if not paths:
        raise RuntimeError(f'No licence material found for {name}; review redistribution before building.')
    return distribution.version, '\n\n'.join(Path(path).read_text(encoding='utf-8') for path in paths)


def license_text(path):
    raw = path.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')


def native_licenses():
    """Conda supplies these separately from its Python licence file."""
    prefix = Path(sys.base_prefix)
    for name in ('zlib', 'libffi', 'openssl', 'bzip2', 'xz', 'libmpdec', 'vc14_runtime', 'ucrt'):
        for record in (prefix / 'conda-meta').glob(name + '-*.json'):
            package = json.loads(record.read_text(encoding='utf-8'))
            if package['name'] != name:
                continue
            identity = f"{name}-{package['version']}-{package['build']}"
            directory = prefix / 'pkgs' / identity / 'info' / 'licenses'
            paths = sorted(path for path in directory.rglob('*') if path.is_file())
            if any(path.suffix.lower() == '.txt' for path in paths):
                paths = [path for path in paths if path.suffix.lower() != '.rtf']
            if not paths:
                raise RuntimeError(f'Missing native licence files for {identity}; do not distribute this build.')
            yield identity + '\n' + '\n\n'.join(license_text(path) for path in paths)


def main():
    sys.path.insert(0, str(ROOT))
    from legal_notice import RIOT_NOTICE, CONTENT_NOTICE, PROTOTYPE_NOTICE
    sections = [
        "THIRD-PARTY NOTICES — prokjs's Set 19 TFT tool\n\n" +
        RIOT_NOTICE + '\n\n' + CONTENT_NOTICE + '\n\n' + PROTOTYPE_NOTICE +
        '\n\nSource attribution is retained in assets/manifest.json, data/set19.json and locale catalog provenance. '
        'Public availability is not redistribution permission. See LEGAL.md before public distribution or monetization.\n'
    ]
    python_licenses = [Path(sys.base_prefix) / name for name in ('LICENSE_PYTHON.txt', 'LICENSE.txt')]
    python_license = next((path for path in python_licenses if path.is_file()), None)
    if python_license is None:
        raise RuntimeError('Python redistribution licence was not found.')
    sections.append('PYTHON ' + sys.version.split()[0] + '\n' + python_license.read_text(encoding='utf-8'))
    tcl_root = Path(tkinter.Tcl().eval('info library'))
    tcl_paths = [tcl_root / 'license.terms', *sorted(
        (Path(sys.base_prefix) / 'pkgs').glob('tk-*/info/licenses/tcl*/license.terms'))]
    tcl_license = next((path for path in tcl_paths if path.is_file()), None)
    tk_license = tcl_root.parent / 'tk8.6' / 'license.terms'
    if tcl_license is None or not tk_license.is_file():
        raise RuntimeError('Tcl/Tk redistribution licence was not found; review this Python distribution.')
    sections.extend(('TCL\n' + tcl_license.read_text(encoding='utf-8'),
                     'TK\n' + tk_license.read_text(encoding='utf-8')))
    sections.extend(native_licenses())
    for package in ('arabic-reshaper', 'pyinstaller'):
        version, wording = package_license(package)
        sections.append(package.upper() + ' ' + version + '\n' + wording)
    sections.append('WINDOWS ICU\nArabic bidirectional display uses the ICU API supplied by the Windows operating system. '
                    'No ICU library or python-bidi/LGPL package is redistributed by this application. '
                    'Windows fonts are system-provided and are not bundled.\n')
    (ROOT / 'THIRD_PARTY_NOTICES.txt').write_text('\n\n' + ('\n\n' + '=' * 72 + '\n\n').join(sections), encoding='utf-8')
    print('Generated THIRD_PARTY_NOTICES.txt from installed dependency licence files.')


if __name__ == '__main__':
    main()
