"""Package-manager integration: metadata, update checks, and uninstall/update commands.

All functions here are blocking; the UI calls them from worker threads.
"""

import glob
import os
import re
import shutil
import subprocess
from typing import Optional

from gi.repository import Gio

from . import appimage
from .scanner import APT, FLATPAK, SNAP, APPIMAGE, LOCAL, App, user_data_dir

APT_ENV = ['env', 'DEBIAN_FRONTEND=noninteractive']


def _run(argv: list, timeout: int = 120) -> tuple:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, '', str(e)


def has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def parse_size(text: str) -> Optional[int]:
    """Parse sizes like '1,1 GB', '502.8 kB' or '120 bytes' (flatpak prints them localized)."""
    m = re.match(r'^\s*([\d.,\u00a0 ]+)\s*([kMGT]?B|bytes?)\s*$', text or '', re.I)
    if not m:
        return None
    number = m.group(1).replace('\u00a0', '').replace(' ', '')
    if ',' in number and '.' in number:  # 1.234,5 or 1,234.5: the last separator is the decimal one
        dec = ',' if number.rfind(',') > number.rfind('.') else '.'
        number = number.replace('.' if dec == ',' else ',', '').replace(dec, '.')
    else:
        number = number.replace(',', '.')
    factor = {'b': 1, 'byte': 1, 'bytes': 1, 'kb': 1e3, 'mb': 1e6, 'gb': 1e9, 'tb': 1e12}[m.group(2).lower()]
    try:
        return int(float(number) * factor)
    except ValueError:
        return None


def _mtime(path: str, follow: bool = True) -> Optional[float]:
    try:
        return os.stat(path).st_mtime if follow else os.lstat(path).st_mtime
    except OSError:
        return None


def _dpkg_list_mtime(package: str) -> Optional[float]:
    # dpkg rewrites <pkg>.list on install and upgrade, so its mtime is the last install/update
    for path in [f'/var/lib/dpkg/info/{package}.list', *glob.glob(f'/var/lib/dpkg/info/{package}:*.list')]:
        t = _mtime(path)
        if t:
            return t
    return None


def human_size(n: float) -> str:
    for unit in ('B', 'kB', 'MB', 'GB'):
        if n < 1000:
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1000
    return f'{n:.1f} TB'


# ---------------------------------------------------------------- metadata

def load_metadata(apps: list) -> None:
    """Fill version / size / origin for every app, in place."""
    apt_info = {}
    if has('dpkg-query'):
        _, out, _ = _run(['dpkg-query', '-W', '-f', '${binary:Package}\t${Version}\t${Installed-Size}\n'])
        for line in out.splitlines():
            parts = line.split('\t')
            if len(parts) == 3:
                size = int(parts[2]) * 1024 if parts[2].isdigit() else None
                apt_info[parts[0].split(':')[0]] = (parts[1], size)

    fp_info = {}
    if has('flatpak'):
        _, out, _ = _run(['flatpak', 'list', '--app',
                          '--columns=application,version,branch,installation,origin,size'])
        for line in out.splitlines():
            parts = line.split('\t')
            if len(parts) == 6:
                app_id, version, branch, inst, origin, size = parts
                fp_info[(app_id, inst)] = (version or branch, size, origin)

    snap_info = {}
    if has('snap'):
        _, out, _ = _run(['snap', 'list'])
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4:
                snap_info[parts[0]] = (parts[1], parts[3], parts[2])

    for app in apps:
        if app.backend == APT and app.package in apt_info:
            version, size = apt_info[app.package]
            app.version = version
            app.size = human_size(size) if size else None
            app.size_bytes = size
            app.installed_at = _dpkg_list_mtime(app.package)
        elif app.backend == FLATPAK:
            info = fp_info.get((app.package, app.installation))
            if info:
                app.version, app.size, app.origin = info
                app.size_bytes = parse_size(app.size)
            base = os.path.expanduser('~/.local/share/flatpak') if app.installation == 'user' else '/var/lib/flatpak'
            # the 'active' link is replaced on every deploy, i.e. on install and update
            app.installed_at = _mtime(f'{base}/app/{app.package}/current/active', follow=False)
        elif app.backend == SNAP and app.package in snap_info:
            app.version, app.origin, rev = snap_info[app.package]
            snap_file = f'/var/lib/snapd/snaps/{app.package}_{rev}.snap'
            if os.path.exists(snap_file):
                app.size_bytes = os.path.getsize(snap_file)
                app.size = human_size(app.size_bytes)
            app.installed_at = _mtime(f'/snap/{app.package}/current', follow=False)
        elif app.backend == APPIMAGE and app.appimage_path:
            version = app.effective.get('X-AppImage-Version')
            # some build tools put a commit hash here, which is useless as a version
            app.version = version if version and not re.fullmatch(r'[0-9a-f]{20,}', version) else None
            try:
                app.size_bytes = os.path.getsize(app.appimage_path)
                app.size = human_size(app.size_bytes)
                app.installed_at = _mtime(app.appimage_path)
            except OSError:
                app.extra['missing'] = True
        if app.backend == LOCAL or app.installed_at is None:
            app.installed_at = _mtime(app.effective.path)


# ---------------------------------------------------------------- updates

def check_updates(apps: list, refresh_apt: bool = False) -> dict:
    """Mark app.update_available. Returns {backend: error string} for failed checks."""
    errors = {}
    backends = {a.backend for a in apps}

    if APT in backends and has('apt'):
        if refresh_apt:
            code, _, err = _run(['pkexec', 'apt-get', 'update'], timeout=600)
            if code != 0:
                errors[APT] = err.strip() or 'apt-get update failed'
        _, out, _ = _run(['apt', 'list', '--upgradable'])
        upgradable = {l.split('/', 1)[0] for l in out.splitlines() if '/' in l}
        for a in apps:
            if a.backend == APT:
                a.update_available = a.package in upgradable

    if FLATPAK in backends and has('flatpak'):
        updates = set()
        for inst in sorted({a.installation for a in apps if a.backend == FLATPAK}):
            code, out, err = _run(['flatpak', 'remote-ls', '--updates', '--app', f'--{inst}',
                                   '--columns=application'], timeout=300)
            if code != 0:
                errors[FLATPAK] = err.strip()
            updates |= {(l.strip(), inst) for l in out.splitlines() if l.strip()}
        for a in apps:
            if a.backend == FLATPAK:
                a.update_available = (a.package, a.installation) in updates

    if SNAP in backends and has('snap'):
        _, out, _ = _run(['snap', 'refresh', '--list'], timeout=300)
        names = {l.split()[0] for l in out.splitlines()[1:] if l.split()}
        for a in apps:
            if a.backend == SNAP:
                a.update_available = a.package in names

    appimages = [a for a in apps if a.backend == APPIMAGE and a.appimage_path and not a.extra.get('missing')]
    if appimages:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            checks = pool.map(lambda a: appimage.check_update(a.appimage_path), appimages)
        failed = []
        for a, check in zip(appimages, checks):
            a.update_available = bool(check.available)
            a.extra['update_check'] = check
            if check.error and check.error != 'No update source':
                failed.append(f'{a.name}: {check.error}')
        if failed:
            errors[APPIMAGE] = '; '.join(failed)

    return errors


# ---------------------------------------------------------------- commands

def can_uninstall(app: App) -> bool:
    if app.backend in (APT, FLATPAK, SNAP):
        return True
    if app.backend == APPIMAGE:
        return app.effective.data_dir == user_data_dir()
    # Unmanaged entries can only be removed when they live in the user's data dir.
    return app.effective.data_dir == user_data_dir()


def can_update(app: App) -> bool:
    if app.backend == APPIMAGE:
        check = app.extra.get('update_check')
        return bool(check and check.download_url)
    return app.backend in (APT, FLATPAK, SNAP)


def simulate_apt_removal(package: str) -> list:
    """Packages apt would remove together with `package`."""
    _, out, _ = _run(['apt-get', '-s', 'remove', package])
    return [l.split()[1] for l in out.splitlines() if l.startswith('Remv ')]


def uninstall_command(app: App, delete_data: bool = False) -> Optional[list]:
    if app.backend == APT:
        return ['pkexec', *APT_ENV, 'apt-get', 'remove', '-y', app.package]
    if app.backend == FLATPAK:
        cmd = ['flatpak', 'uninstall', '-y', '--noninteractive', f'--{app.installation}', app.package]
        if delete_data:
            cmd.insert(3, '--delete-data')
        return cmd
    if app.backend == SNAP:
        return ['pkexec', 'snap', 'remove', app.package]
    if app.backend in (APPIMAGE, LOCAL):
        return lambda op: remove_files(app)
    return None


def remove_files(app: App) -> tuple:
    """Trash an AppImage (file, entry, private icon) or a local entry."""
    paths = [app.effective.path]
    if app.backend == APPIMAGE:
        paths = appimage.removal_paths(app.appimage_path, app.effective.path, app.icon)
        appimage.forget(app.appimage_path)
    for p in paths:
        Gio.File.new_for_path(p).trash(None)
    return True, '\n'.join(f'Moved to trash: {p}' for p in paths)


def update_command(apps: list) -> list:
    """Commands that update the given apps, grouped per backend."""
    cmds = []
    apt_pkgs = sorted({a.package for a in apps if a.backend == APT})
    if apt_pkgs:
        cmds.append(['pkexec', *APT_ENV, 'apt-get', 'install', '--only-upgrade', '-y', *apt_pkgs])
    for inst in ('system', 'user'):
        ids = sorted({a.package for a in apps if a.backend == FLATPAK and a.installation == inst})
        if ids:
            cmds.append(['flatpak', 'update', '-y', '--noninteractive', f'--{inst}', *ids])
    snaps = sorted({a.package for a in apps if a.backend == SNAP})
    if snaps:
        cmds.append(['pkexec', 'snap', 'refresh', *snaps])
    for a in apps:
        check = a.extra.get('update_check')
        if a.backend == APPIMAGE and check and check.download_url:
            cmds.append(lambda op, a=a, url=check.download_url: download_appimage_update(a, url, op))
    return cmds


class Operation:
    """A running sequence of commands: streams output, reports status, and can be cancelled.

    Callbacks are invoked from the worker thread; the UI marshals them to the main loop.
    """

    def __init__(self, on_output=None, on_status=None, on_cancellable=None):
        self.on_output = on_output or (lambda line: None)
        self.on_status = on_status or (lambda text: None)
        self.on_cancellable = on_cancellable or (lambda cancellable: None)
        self.cancelled = False
        self._proc = None
        self._cancellable = False
        self._last_line = None

    def output(self, line: str):
        line = line.rstrip()
        if line and line != self._last_line:  # progress output often repeats itself
            self._last_line = line
            self.on_output(line)

    def status(self, text: str):
        self.on_status(text)

    def set_cancellable(self, cancellable: bool):
        self._cancellable = cancellable
        self.on_cancellable(cancellable)

    def cancel(self):
        if not self._cancellable:
            return
        self.cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run_argv(self, argv: list) -> tuple:
        """Run a command, streaming its output. Returns (exit code, full output)."""
        # Interrupting apt/dpkg or snapd mid-way can leave the system half-configured, and a
        # pkexec'd root process can't be signalled anyway, so only unprivileged commands can stop.
        self.set_cancellable(argv[0] != 'pkexec')
        self.output(f'$ {" ".join(argv)}')
        chunks, pending = [], ''
        try:
            self._proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL)
        except OSError as e:
            self.set_cancellable(False)
            return -1, str(e)
        while True:
            data = os.read(self._proc.stdout.fileno(), 4096)
            if not data:
                break
            text = data.decode('utf-8', 'replace')
            chunks.append(text)
            pending += text
            *lines, pending = re.split(r'[\r\n]', pending)
            for line in lines:
                self.output(line)
        self.output(pending)
        code = self._proc.wait()
        self._proc = None
        self.set_cancellable(False)
        return code, ''.join(chunks)


def run_commands(cmds: list, op: Optional[Operation] = None) -> tuple:
    """Run commands (argv lists, or callables taking the Operation and returning (ok, log)) in
    sequence, stopping at the first failure or cancellation. Returns (ok, combined output)."""
    op = op or Operation()
    log = []
    for cmd in cmds:
        if op.cancelled:
            log.append('Cancelled.')
            return False, '\n'.join(log)
        if callable(cmd):
            ok, out = cmd(op)
            op.output(out)
            log.append(out)
            if not ok:
                return False, '\n'.join(log)
            continue
        code, out = op.run_argv(cmd)
        log.append(f'$ {" ".join(cmd)}\n{out}')
        if op.cancelled:
            log.append('Cancelled.')
            return False, '\n'.join(log)
        if code != 0:
            if code in (126, 127) and cmd[0] == 'pkexec':
                log.append('Authentication was cancelled or failed.')
            return False, '\n'.join(log)
    return True, '\n'.join(log)


def download_appimage_update(app: App, url: str, op: Operation) -> tuple:
    """Download step for run_commands: reports progress and stops when the operation is cancelled."""
    op.set_cancellable(True)
    last = [-1]

    def progress(fraction):
        pct = int(fraction * 100)
        if pct != last[0]:
            last[0] = pct
            op.status(f'Downloading {app.name}… {pct}%')
            if pct % 10 == 0:
                op.output(f'{pct}%')
    op.output(f'Downloading {url}')
    try:
        return appimage.download_update(app.appimage_path, url, progress, lambda: op.cancelled)
    finally:
        op.set_cancellable(False)
