"""Native AppImage management: integration, removal, update sources, update checks and downloads.

Layout: ~/AppImages/<name>.appimage, icons in ~/AppImages/.icons, entries in
~/.local/share/applications/<name>.desktop. Metadata is extracted with 7z, which reads the SquashFS image without
executing anything from the AppImage.
"""

import configparser
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from .entries import set_key
from .scanner import parse_desktop_file, user_data_dir

APPIMAGES_DIR = os.path.expanduser('~/AppImages')
ICONS_DIR = os.path.join(APPIMAGES_DIR, '.icons')
CONFIG_PATH = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'),
                           'app-commander', 'appimage-updates.json')
GEARLEVER_CONF = os.path.expanduser('~/.var/app/it.mijorus.gearlever/config/gearlever.conf')
MANAGED_MARKER = 'X-AppCommander-Managed'
USER_AGENT = 'AppCommander (+https://github.com/4mbrus/app-commander)'


def applications_dir() -> str:
    return os.path.join(user_data_dir(), 'applications')


def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9._-]+', '_', name.lower()).strip('_') or 'appimage'


# ---------------------------------------------------------------- ELF / embedded update info

def read_update_info(path: str) -> Optional[str]:
    """Return the contents of the ELF `.upd_info` section (AppImage spec), if any."""
    try:
        with open(path, 'rb') as f:
            ident = f.read(16)
            if ident[:4] != b'\x7fELF':
                return None
            is64, endian = ident[4] == 2, '<' if ident[5] == 1 else '>'
            if is64:
                f.seek(0x28); shoff, = struct.unpack(endian + 'Q', f.read(8))
                f.seek(0x3A); shentsize, shnum, shstrndx = struct.unpack(endian + 'HHH', f.read(6))
            else:
                f.seek(0x20); shoff, = struct.unpack(endian + 'I', f.read(4))
                f.seek(0x2E); shentsize, shnum, shstrndx = struct.unpack(endian + 'HHH', f.read(6))
            if not shoff or shnum == 0 or shnum > 512:
                return None

            def section(i):
                f.seek(shoff + i * shentsize)
                raw = f.read(shentsize)
                if is64:
                    name, _t, _fl, _addr, off, size = struct.unpack(endian + 'IIQQQQ', raw[:40])
                else:
                    name, _t, _fl, _addr, off, size = struct.unpack(endian + 'IIIIII', raw[:24])
                return name, off, size

            _, str_off, str_size = section(shstrndx)
            f.seek(str_off); names = f.read(str_size)
            for i in range(shnum):
                name_off, off, size = section(i)
                if names[name_off:names.index(b'\0', name_off)] == b'.upd_info':
                    f.seek(off)
                    data = f.read(min(size, 4096)).split(b'\0', 1)[0].decode('utf-8', 'replace').strip()
                    return data or None
    except (OSError, struct.error, ValueError):
        return None
    return None


# ---------------------------------------------------------------- update sources

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return _import_gearlever_config()


def _save_config(config: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


def _import_gearlever_config() -> dict:
    """One-time import of the update sources configured in Gear Lever."""
    config = {}
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(GEARLEVER_CONF)
    except configparser.Error:
        return config
    for section in parser.sections():
        if not section.endswith('.update_manager'):
            continue
        app_section = section.removesuffix('.update_manager')
        path = parser.get(app_section, 'file_path', fallback=None)
        um = parser[section]
        manager = um.get('manager')
        if not path or not manager:
            continue
        if manager == 'GithubUpdater' and um.get('repo'):
            config[path] = {'type': 'github', 'repo': um.get('repo'), 'pattern': um.get('repo_filename', ''),
                            'prereleases': um.get('allow_prereleases', 'False') == 'True'}
        elif manager == 'StaticFileUpdater' and um.get('url'):
            config[path] = {'type': 'url', 'url': um.get('url')}
    if config:
        _save_config(config)
    return config


def get_custom_source(appimage_path: str) -> Optional[dict]:
    return _load_config().get(appimage_path)


def set_custom_source(appimage_path: str, source: Optional[dict]):
    config = _load_config()
    if source:
        config[appimage_path] = source
    else:
        config.pop(appimage_path, None)
    _save_config(config)


def _move_custom_source(old: str, new: str):
    config = _load_config()
    if old in config:
        config[new] = config.pop(old)
        _save_config(config)


def parse_embedded(info: str) -> Optional[dict]:
    parts = info.split('|')
    if parts[0] == 'gh-releases-zsync' and len(parts) == 5:
        return {'type': 'github', 'repo': f'{parts[1]}/{parts[2]}', 'pattern': parts[4],
                'prereleases': parts[3] in ('latest-pre', 'latest-all'),
                'tag': None if parts[3].startswith('latest') else parts[3]}
    if parts[0] == 'zsync' and len(parts) == 2 and parts[1].startswith('http'):
        return {'type': 'zsync', 'url': parts[1]}
    return None


def get_update_source(appimage_path: str) -> tuple:
    """(source dict or None, origin) where origin is 'custom', 'embedded' or None."""
    custom = get_custom_source(appimage_path)
    if custom:
        return custom, 'custom'
    info = read_update_info(appimage_path)
    embedded = parse_embedded(info) if info else None
    return (embedded, 'embedded') if embedded else (None, None)


def describe_source(source: Optional[dict]) -> str:
    if not source:
        return 'None'
    if source['type'] == 'github':
        return f'GitHub releases of {source["repo"]} matching “{source["pattern"]}”'
    return source.get('url', '')


# ---------------------------------------------------------------- update checks

@dataclass
class UpdateCheck:
    available: Optional[bool]  # None = could not tell
    download_url: Optional[str] = None
    size: Optional[int] = None
    error: Optional[str] = None
    extra: dict = field(default_factory=dict)


def _http(url: str, method: str = 'GET', accept: str = None, timeout: int = 30):
    headers = {'User-Agent': USER_AGENT}
    if accept:
        headers['Accept'] = accept
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=timeout)


def _file_hash(path: str, alg: str) -> str:
    h = hashlib.new(alg)
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _zsync_header(url: str) -> dict:
    with _http(url) as r:
        head = r.read(16384).split(b'\n\n', 1)[0].decode('utf-8', 'replace')
    out = {}
    for line in head.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            out[k.strip()] = v.strip()
    return out


def _check_zsync(path: str, zsync_url: str) -> UpdateCheck:
    header = _zsync_header(zsync_url)
    sha1 = header.get('SHA-1')
    target = urllib.parse.urljoin(zsync_url, header.get('URL', ''))
    size = int(header['Length']) if header.get('Length', '').isdigit() else None
    if not sha1:
        return UpdateCheck(None, target, size, 'zsync file has no SHA-1')
    return UpdateCheck(sha1 != _file_hash(path, 'sha1'), target, size)


def _pick_asset(assets: list, pattern: str) -> Optional[dict]:
    matches = [a for a in assets if fnmatch.fnmatch(a['name'], pattern)]
    if len(matches) > 1:
        arm = re.compile(r'arm|aarch64', re.I)
        matches = [a for a in matches if not arm.search(a['name'])] or matches
    return matches[0] if matches else None


def _check_github(path: str, source: dict) -> UpdateCheck:
    base = f'https://api.github.com/repos/{source["repo"]}/releases'
    if source.get('tag'):
        url = f'{base}/tags/{urllib.parse.quote(source["tag"])}'
    else:
        url = base if source.get('prereleases') else f'{base}/latest'
    with _http(url, accept='application/vnd.github+json') as r:
        data = json.load(r)
    releases = data if isinstance(data, list) else [data]

    for release in releases:
        if release.get('draft'):
            continue
        asset = _pick_asset(release.get('assets', []), source['pattern'])
        if not asset:
            continue
        if asset['name'].endswith('.zsync'):
            target = next((a for a in release['assets'] if a['name'] == asset['name'][:-6]), None)
            if not target:
                continue
            check = _check_zsync(path, asset['browser_download_url'])
            check.download_url, check.size = target['browser_download_url'], target['size']
        else:
            check = UpdateCheck(None, asset['browser_download_url'], asset['size'])
            digest = asset.get('digest') or ''
            if digest.startswith('sha256:'):
                check.available = digest[7:] != _file_hash(path, 'sha256')
            else:
                check.available = asset['size'] != os.path.getsize(path)
        check.extra['version'] = release.get('tag_name')
        return check
    return UpdateCheck(None, error=f'No release asset matches “{source["pattern"]}”')


def _check_url(path: str, url: str, known_etag: Optional[str] = None) -> UpdateCheck:
    with _http(url, method='HEAD') as r:
        length = r.headers.get('Content-Length')
        etag = r.headers.get('ETag')
    if etag and known_etag:
        return UpdateCheck(etag != known_etag, url, int(length) if length and length.isdigit() else None)
    if not length or not length.isdigit():
        return UpdateCheck(None, url, error='Server did not report a file size')
    return UpdateCheck(int(length) != os.path.getsize(path), url, int(length))


def check_update(appimage_path: str) -> UpdateCheck:
    source, _origin = get_update_source(appimage_path)
    if not source:
        return UpdateCheck(None, error='No update source')
    try:
        if source['type'] == 'github':
            return _check_github(appimage_path, source)
        if source['type'] == 'zsync':
            return _check_zsync(appimage_path, source['url'])
        return _check_url(appimage_path, source['url'], source.get('etag'))
    except urllib.error.HTTPError as e:
        hint = ' (GitHub rate limit, try again later)' if e.code == 403 else ''
        return UpdateCheck(None, error=f'HTTP {e.code}{hint}')
    except (OSError, ValueError, KeyError) as e:
        return UpdateCheck(None, error=str(e))


def download_update(appimage_path: str, url: str, progress: Callable[[float], None] = None,
                    cancelled: Callable[[], bool] = None) -> tuple:
    """Replace the AppImage in place with the file at `url`. Returns (ok, log)."""
    directory = os.path.dirname(appimage_path)
    fd, tmp = tempfile.mkstemp(prefix='.download-', dir=directory)
    try:
        with _http(url, timeout=60) as r, os.fdopen(fd, 'wb') as out:
            total = int(r.headers.get('Content-Length') or 0)
            etag = r.headers.get('ETag')
            done = 0
            while chunk := r.read(1 << 20):
                if cancelled and cancelled():
                    raise InterruptedError('cancelled')
                out.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
        with open(tmp, 'rb') as f:
            if f.read(4) != b'\x7fELF':
                raise ValueError('Downloaded file is not an AppImage')
        os.chmod(tmp, 0o755)
        os.replace(tmp, appimage_path)
        # remember which server version we have, so same-size releases are still detected
        source = get_custom_source(appimage_path)
        if source and source.get('type') == 'url' and etag:
            set_custom_source(appimage_path, {**source, 'etag': etag})
        return True, f'Downloaded {url}\nReplaced {appimage_path}'
    except (OSError, ValueError) as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return False, f'Download of {url} failed: {e}'


# ---------------------------------------------------------------- inspection / integration

@dataclass
class AppImageInfo:
    path: str
    name: str
    comment: str = ''
    version: Optional[str] = None
    icon_path: Optional[str] = None
    desktop_text: Optional[str] = None
    desktop_entry: dict = field(default_factory=dict)
    update_info: Optional[str] = None
    tmpdir: Optional[str] = None

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def destination(self) -> str:
        return os.path.join(APPIMAGES_DIR, f'{self.slug}.appimage')

    @property
    def desktop_path(self) -> str:
        return os.path.join(applications_dir(), f'{self.slug}.desktop')

    def cleanup(self):
        if self.tmpdir:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            self.tmpdir = None


def is_appimage(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            head = f.read(11)
    except OSError:
        return False
    # ELF magic + AppImage magic bytes "AI\x02" (type 2) or "AI\x01" (type 1) at offset 8
    return head[:4] == b'\x7fELF' and head[8:10] == b'AI'


def _inside(root: str, path: str) -> Optional[str]:
    """Resolve path (following symlinks) and refuse anything escaping the extraction root."""
    real = os.path.realpath(path)
    return real if real.startswith(os.path.realpath(root) + os.sep) and os.path.isfile(real) else None


def inspect(path: str) -> AppImageInfo:
    if not is_appimage(path):
        raise ValueError('This file is not an AppImage')
    if not shutil.which('7z'):
        raise RuntimeError('7z is needed to read AppImages (install the “7zip” package)')
    tmpdir = tempfile.mkdtemp(prefix='app-commander-')
    root = os.path.join(tmpdir, 'root')
    subprocess.run(['7z', 'x', path, f'-o{root}', '-y', '-bso0', '-bsp0',
                    '-ir!*.desktop', '-ir0!.DirIcon', '-ir0!*.png', '-ir0!*.svg',
                    '-ir!usr/share/icons/*'], capture_output=True, timeout=120)

    info = AppImageInfo(path=path, name=os.path.basename(path).rsplit('.', 1)[0], tmpdir=tmpdir,
                        update_info=read_update_info(path))
    desktop_files = sorted(f for f in os.listdir(root) if f.endswith('.desktop')) if os.path.isdir(root) else []
    desktop = _inside(root, os.path.join(root, desktop_files[0])) if desktop_files else None
    if desktop:
        with open(desktop, encoding='utf-8', errors='replace') as f:
            info.desktop_text = f.read()
        entry = parse_desktop_file(desktop) or {}
        info.desktop_entry = entry
        info.name = entry.get('Name') or info.name
        info.comment = entry.get('Comment', '')
        info.version = entry.get('X-AppImage-Version')

    icon_name = info.desktop_entry.get('Icon', '')
    candidates = [os.path.join(root, f'{icon_name}{ext}') for ext in ('.svg', '.png')] if icon_name else []
    candidates.append(os.path.join(root, '.DirIcon'))
    info.icon_path = next((p for p in (_inside(root, c) for c in candidates if os.path.lexists(c)) if p), None)
    return info


def desktop_quote(arg: str) -> str:
    """Quote an Exec argument per the Desktop Entry spec (double quotes, not shell single quotes)."""
    if arg.startswith('%') or not re.search(r'[\s"\'\\><~|&;$*?#()`]', arg):
        return arg
    return '"' + re.sub(r'(["`$\\])', r'\\\1', arg) + '"'


def _exec_args(exec_line: str) -> str:
    """Arguments of an Exec line without the program (e.g. 'AppRun --no-sandbox %U' -> '--no-sandbox %U')."""
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = exec_line.split()
    while tokens and (tokens[0] == 'env' or re.match(r'^\w+=', tokens[0])):
        tokens.pop(0)
    return ' '.join(desktop_quote(t) for t in tokens[1:])


def build_desktop_entry(info: AppImageInfo, appimage_path: str, icon_path: Optional[str]) -> str:
    text = info.desktop_text or f'[Desktop Entry]\nType=Application\nName={info.name}\nExec=x\n'
    program = desktop_quote(appimage_path)
    out, group = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('['):
            group = stripped
        elif group and group.startswith('[Desktop') and stripped.startswith('Exec='):
            args = _exec_args(stripped[5:])
            line = f'Exec={program} {args}'.rstrip()
        elif group == '[Desktop Entry]' and stripped.startswith('TryExec='):
            continue
        out.append(line)
    text = '\n'.join(out) + '\n'
    text = set_key(text, 'TryExec', appimage_path)
    if icon_path:
        text = set_key(text, 'Icon', icon_path)
    text = set_key(text, 'X-AppImage-Name', info.name)
    if info.version:
        text = set_key(text, 'X-AppImage-Version', info.version)
    return set_key(text, MANAGED_MARKER, 'true')


def integrate(info: AppImageInfo, keep_original: bool = False) -> str:
    """Install the AppImage into ~/AppImages and create its desktop entry. Returns the entry path."""
    os.makedirs(APPIMAGES_DIR, exist_ok=True)
    os.makedirs(ICONS_DIR, exist_ok=True)
    os.makedirs(applications_dir(), exist_ok=True)

    dest = info.destination
    if os.path.realpath(info.path) != os.path.realpath(dest):
        tmp = dest + '.part'
        shutil.copy2(info.path, tmp)
        os.replace(tmp, dest)
        if not keep_original:
            os.unlink(info.path)
    os.chmod(dest, 0o755)

    icon = None
    if info.icon_path:
        icon = os.path.join(ICONS_DIR, info.slug)
        shutil.copyfile(info.icon_path, icon)

    with open(info.desktop_path, 'w', encoding='utf-8') as f:
        f.write(build_desktop_entry(info, dest, icon))
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', applications_dir()], capture_output=True)
    if info.path != dest:
        _move_custom_source(info.path, dest)
    return info.desktop_path


def removal_paths(appimage_path: Optional[str], desktop_path: str, icon: str) -> list:
    """Files that belong to an integrated AppImage: the AppImage, its entry, and its private icon."""
    paths = [desktop_path]
    if appimage_path and os.path.exists(appimage_path):
        paths.append(appimage_path)
    if icon and os.path.isabs(icon) and os.path.realpath(icon).startswith(os.path.realpath(ICONS_DIR) + os.sep) \
            and os.path.exists(icon):
        paths.append(icon)
    return paths


def forget(appimage_path: str):
    set_custom_source(appimage_path, None)
