"""Editing desktop entries through user-level overrides (like a menu editor)."""

import hashlib
import os
import re

from .scanner import App, user_data_dir

OVERRIDE_MARKER = 'X-AppCommander-Override'


def set_key(text: str, key: str, value: str) -> str:
    """Set `key=value` inside the [Desktop Entry] group of a desktop file's text."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == '[Desktop Entry]'), None)
    if start is None:
        return f'[Desktop Entry]\n{key}={value}\n' + text
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith('[')), len(lines))
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=')
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            lines[i] = f'{key}={value}'
            break
    else:
        # insert after the last non-blank line of the group
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f'{key}={value}')
    return '\n'.join(lines) + '\n'


def user_entry_path(app: App) -> str:
    return os.path.join(user_data_dir(), 'applications', app.desktop_id)


def ensure_user_copy(app: App) -> str:
    """Return a writable entry for the app, copying the system entry into the user dir if needed."""
    path = user_entry_path(app)
    if os.path.exists(path):
        return path
    with open(app.effective.path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    text = set_key(text, OVERRIDE_MARKER, 'true')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def set_hidden(app: App, hidden: bool) -> str:
    path = ensure_user_copy(app)
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    with open(path, 'w', encoding='utf-8') as f:
        f.write(set_key(text, 'NoDisplay', 'true' if hidden else 'false'))
    return path


def is_our_override(path: str) -> bool:
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f'{OVERRIDE_MARKER}=true' in f.read()
    except OSError:
        return False


def _group_bounds(lines: list) -> tuple:
    start = next((i for i, l in enumerate(lines) if l.strip() == '[Desktop Entry]'), None)
    if start is None:
        return None, None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith('[')), len(lines))
    return start, end


def keys_in_group(text: str) -> dict:
    lines = text.splitlines()
    start, end = _group_bounds(lines)
    out = {}
    if start is None:
        return out
    for line in lines[start + 1:end]:
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def remove_key(text: str, key: str) -> str:
    lines = text.splitlines()
    start, end = _group_bounds(lines)
    if start is None:
        return text
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=')
    kept = lines[:start + 1] + [l for l in lines[start + 1:end] if not pattern.match(l)] + lines[end:]
    return '\n'.join(kept) + '\n'


def set_localized(text: str, key: str, value: str) -> str:
    """Set `key` and any translation of it for the current locale, so the change is what users see.
    An empty value removes the key (and those translations)."""
    from .scanner import _locale_variants
    present = keys_in_group(text)
    keys = [key] + [f'{key}[{loc}]' for loc in _locale_variants() if f'{key}[{loc}]' in present]
    for k in keys:
        text = set_key(text, k, value) if value else remove_key(text, k)
    return text


def editable_path(app: App) -> str:
    """The entry file to edit: the user's own entry, or a fresh override copy of a packaged one.
    (Flatpak's user exports are writable but owned by flatpak, so writability alone isn't enough.)"""
    if app.effective.data_dir == user_data_dir() and os.access(app.effective.path, os.W_OK):
        return app.effective.path
    return ensure_user_copy(app)


def apply_edits(app: App, edits: dict) -> str:
    """Write edits ({key: value or ''}) to a user-writable copy of the entry. Returns its path."""
    path = editable_path(app)
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    for key, value in edits.items():
        text = set_localized(text, key, value)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def store_icon(app: App, source: str) -> str:
    """Copy a chosen image next to user data so the entry keeps working if the original moves."""
    icons = os.path.join(user_data_dir(), 'app-commander', 'icons')
    os.makedirs(icons, exist_ok=True)
    with open(source, 'rb') as f:
        data = f.read()
    stem = app.desktop_id.removesuffix('.desktop')
    ext = os.path.splitext(source)[1].lower() or '.png'
    # content hash in the name: the shell caches icons by path, so a new image needs a new path
    dest = os.path.join(icons, f'{stem}-{hashlib.sha1(data).hexdigest()[:8]}{ext}')
    for old in os.listdir(icons):
        if re.fullmatch(re.escape(stem) + r'-[0-9a-f]{8}\.\w+', old) and os.path.join(icons, old) != dest:
            os.unlink(os.path.join(icons, old))
    with open(dest, 'wb') as out:
        out.write(data)
    return dest


# ---------------------------------------------------------------- autostart

def user_autostart_dir() -> str:
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'autostart')


def system_autostart_dirs() -> list:
    dirs = os.environ.get('XDG_CONFIG_DIRS') or '/etc/xdg'
    return [os.path.join(d, 'autostart') for d in dirs.split(':') if d]


def _autostart_file(app: App):
    """The autostart file that applies to the app (the user's one wins), or None."""
    for d in [user_autostart_dir()] + system_autostart_dirs():
        path = os.path.join(d, app.desktop_id)
        if os.path.isfile(path):
            return path
    return None


def is_autostart(app: App) -> bool:
    path = _autostart_file(app)
    if not path:
        return False
    with open(path, encoding='utf-8', errors='replace') as f:
        keys = keys_in_group(f.read())
    return keys.get('Hidden', 'false') != 'true' and keys.get('X-GNOME-Autostart-enabled', 'true') != 'false'


def set_autostart(app: App, enabled: bool) -> str:
    """Enable or disable starting the app at login. An existing autostart file (which may carry
    app-specific options such as --minimized) is kept and only switched on or off."""
    user_path = os.path.join(user_autostart_dir(), app.desktop_id)
    source = _autostart_file(app)
    if not source and not enabled:
        return user_path
    with open(source or app.effective.path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    text = set_key(text, 'Hidden', 'false' if enabled else 'true')
    text = set_key(text, 'X-GNOME-Autostart-enabled', 'true' if enabled else 'false')
    os.makedirs(os.path.dirname(user_path), exist_ok=True)
    with open(user_path, 'w', encoding='utf-8') as f:
        f.write(text)
    return user_path


# ---------------------------------------------------------------- new launchers

CREATED_MARKER = 'X-AppCommander-Created'


def _escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace('\t', '\\t')


def create_launcher(name: str, command: str, terminal: bool = False) -> str:
    """Write a new desktop entry to the user's applications folder. Returns its path."""
    directory = os.path.join(user_data_dir(), 'applications')
    os.makedirs(directory, exist_ok=True)
    slug = re.sub(r'[^a-z0-9._-]+', '-', name.lower()).strip('-') or 'launcher'
    path, n = os.path.join(directory, f'{slug}.desktop'), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(directory, f'{slug}-{n}.desktop')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('[Desktop Entry]\n'
                'Type=Application\n'
                f'Name={_escape(name)}\n'
                f'Exec={_escape(command)}\n'
                'Icon=application-x-executable\n'
                f'Terminal={"true" if terminal else "false"}\n'
                f'{CREATED_MARKER}=true\n')
    return path
