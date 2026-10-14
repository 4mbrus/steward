"""Discovery of desktop entries and detection of the package backend that owns them.

Pure Python (no GTK) so it can be unit tested.
"""

import glob
import locale
import os
import shlex
import shutil
from dataclasses import dataclass, field
from typing import Optional

APT = 'apt'
FLATPAK = 'flatpak'
SNAP = 'snap'
APPIMAGE = 'appimage'
LOCAL = 'local'

BACKEND_LABELS = {
    APT: 'System package',
    FLATPAK: 'Flatpak',
    SNAP: 'Snap',
    APPIMAGE: 'AppImage',
    LOCAL: 'Local entry',
}

SNAP_APPS_DIR = '/var/lib/snapd/desktop/applications'


@dataclass
class DesktopFile:
    path: str
    desktop_id: str
    data_dir: str
    entry: dict  # raw key -> value of the [Desktop Entry] group

    def get(self, key: str, default: str = '') -> str:
        return self.entry.get(key, default)

    def get_localized(self, key: str) -> str:
        for loc in _locale_variants():
            val = self.entry.get(f'{key}[{loc}]')
            if val:
                return val
        return self.entry.get(key, '')

    def get_bool(self, key: str) -> bool:
        return self.entry.get(key, 'false').strip().lower() == 'true'


@dataclass
class App:
    desktop_id: str
    files: list  # DesktopFile, highest precedence first
    backend: str = LOCAL
    package: Optional[str] = None  # apt pkg / flatpak app id / snap name
    installation: Optional[str] = None  # flatpak: 'system' | 'user'
    appimage_path: Optional[str] = None
    version: Optional[str] = None
    size: Optional[str] = None
    size_bytes: Optional[int] = None
    installed_at: Optional[float] = None  # install or last update time (epoch seconds)
    origin: Optional[str] = None
    update_available: bool = False
    broken: Optional[str] = None  # the missing program, when the entry can't launch
    extra: dict = field(default_factory=dict)

    @property
    def effective(self) -> DesktopFile:
        return self.files[0]

    @property
    def name(self) -> str:
        return self.effective.get_localized('Name') or self.desktop_id.removesuffix('.desktop')

    @property
    def comment(self) -> str:
        return self.effective.get_localized('Comment') or self.effective.get_localized('GenericName')

    @property
    def icon(self) -> str:
        return self.effective.get('Icon')

    @property
    def hidden(self) -> bool:
        e = self.effective
        return e.get_bool('NoDisplay') or e.get_bool('Hidden')

    @property
    def is_overridden(self) -> bool:
        return len(self.files) > 1

    @property
    def user_override(self) -> Optional[DesktopFile]:
        """The effective file if it lives in the user's data dir and shadows another entry."""
        if self.is_overridden and self.effective.data_dir == user_data_dir():
            return self.files[0]
        return None

    def search_text(self) -> str:
        keywords = self.effective.get_localized('Keywords').replace(';', ' ')
        return ' '.join([self.name, self.comment, keywords, self.desktop_id, self.package or '']).lower()


def _locale_variants() -> list:
    lang = os.environ.get('LC_ALL') or os.environ.get('LC_MESSAGES') or os.environ.get('LANG') \
        or (locale.getlocale()[0] or '')
    lang = lang.split('.')[0].split('@')[0]
    if not lang or lang in ('C', 'POSIX'):
        return []
    out = [lang]
    if '_' in lang:
        out.append(lang.split('_')[0])
    return out


def user_data_dir() -> str:
    return os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')


def data_dirs() -> list:
    """XDG data dirs in precedence order, plus well-known package export dirs."""
    dirs = [user_data_dir()]
    env = os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share'
    dirs += [d for d in env.split(':') if d]
    for extra in (os.path.expanduser('~/.local/share/flatpak/exports/share'),
                  '/var/lib/flatpak/exports/share',
                  '/var/lib/snapd/desktop'):
        dirs.append(extra)
    seen, out = set(), []
    for d in dirs:
        d = os.path.normpath(d)
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def parse_desktop_file(path: str) -> Optional[dict]:
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    entry, in_group = {}, False
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('['):
            in_group = line == '[Desktop Entry]'
            continue
        if in_group and '=' in line:
            k, v = line.split('=', 1)
            entry[k.strip()] = v.strip()
    if entry.get('Type', 'Application') != 'Application':
        return None
    return entry


def collect_desktop_files(dirs: Optional[list] = None) -> dict:
    """Return {desktop_id: [DesktopFile, ...]} ordered by precedence."""
    result = {}
    for data_dir in dirs or data_dirs():
        apps_dir = os.path.join(data_dir, 'applications')
        if not os.path.isdir(apps_dir):
            continue
        for path in sorted(glob.glob(os.path.join(apps_dir, '**', '*.desktop'), recursive=True)):
            entry = parse_desktop_file(path)
            if entry is None:
                continue
            desktop_id = os.path.relpath(path, apps_dir).replace(os.sep, '-')
            result.setdefault(desktop_id, []).append(DesktopFile(path, desktop_id, data_dir, entry))
    return result


def load_dpkg_owners(info_dir: str = '/var/lib/dpkg/info') -> dict:
    """Map of .desktop file path -> owning Debian package."""
    owners = {}
    for list_file in glob.glob(os.path.join(info_dir, '*.list')):
        pkg = os.path.basename(list_file)[:-5].split(':')[0]
        try:
            with open(list_file, encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.rstrip('\n')
                    if line.endswith('.desktop'):
                        owners[line] = pkg
        except OSError:
            pass
    return owners


def find_appimage_path(df: DesktopFile) -> Optional[str]:
    candidates = []
    if df.get('TryExec'):
        candidates.append(df.get('TryExec'))
    try:
        candidates += shlex.split(df.get('Exec'))
    except ValueError:
        candidates += df.get('Exec').split()
    for c in candidates:
        if c.lower().endswith('.appimage'):
            return os.path.expanduser(c)
    return None


def _program_exists(program: str) -> bool:
    if os.path.isabs(program):
        return os.path.isfile(program) and os.access(program, os.X_OK)
    return shutil.which(program) is not None


def missing_program(df: DesktopFile) -> Optional[str]:
    """The program a desktop entry needs but which isn't installed (as GLib's TryExec/Exec check)."""
    try_exec = df.get('TryExec')
    if try_exec and not _program_exists(os.path.expanduser(try_exec)):
        return try_exec
    try:
        tokens = shlex.split(df.get('Exec'))
    except ValueError:
        tokens = df.get('Exec').split()
    if not tokens:
        return None
    program = tokens[0]
    if program == 'env':  # env VAR=1 program ...
        rest = [t for t in tokens[1:] if '=' not in t and not t.startswith('-')]
        program = rest[0] if rest else program
    return None if _program_exists(os.path.expanduser(program)) else program


def classify(df: DesktopFile, dpkg_owners: dict) -> tuple:
    """Return (backend, package, installation, appimage_path) for a single file."""
    path = df.path
    real = os.path.realpath(path)
    fp_id = df.get('X-Flatpak')
    if fp_id or '/flatpak/exports/' in path:
        installation = 'user' if path.startswith(os.path.expanduser('~')) else 'system'
        return FLATPAK, fp_id or df.desktop_id.removesuffix('.desktop'), installation, None
    if path.startswith(SNAP_APPS_DIR) or df.get('X-SnapInstanceName'):
        name = df.get('X-SnapInstanceName') or df.desktop_id.split('_', 1)[0]
        return SNAP, name, None, None
    appimage = find_appimage_path(df)
    if appimage:
        return APPIMAGE, None, None, appimage
    for p in (path, real):
        if p in dpkg_owners:
            return APT, dpkg_owners[p], None, None
        # usrmerge: dpkg may list /usr/share/... while the file is reached via /share
        alt = p.removeprefix('/usr') if p.startswith('/usr/') else '/usr' + p
        if alt in dpkg_owners:
            return APT, dpkg_owners[alt], None, None
    return LOCAL, None, None, None


def build_apps(collected: dict, dpkg_owners: dict) -> list:
    apps = []
    for desktop_id, files in collected.items():
        app = App(desktop_id=desktop_id, files=files, broken=missing_program(files[0]))
        # The first file owned by a package decides the backend, so a user override
        # of a packaged entry is still attributed to that package.
        for df in files:
            backend, package, installation, appimage = classify(df, dpkg_owners)
            if backend != LOCAL:
                app.backend, app.package, app.installation, app.appimage_path = \
                    backend, package, installation, appimage
                break
        apps.append(app)
    apps.sort(key=lambda a: a.name.lower())
    return apps


def scan() -> list:
    return build_apps(collect_desktop_files(), load_dpkg_owners())
