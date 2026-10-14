# App Commander

A GTK4 / libadwaita app that lists every installed application on your system,
shows where its desktop entry lives, and lets you launch, hide, edit, update or
uninstall it, whatever format it came from.

Inspired by [Gear Lever](https://github.com/mijorus/gearlever) and
[Main Menu](https://codeberg.org/libre-menu-editor/libre-menu-editor).

| Source     | Detected by                                     | Uninstall                          | Updates                       |
|------------|-------------------------------------------------|------------------------------------|-------------------------------|
| apt / .deb | `/var/lib/dpkg/info/*.list` owns the entry      | `pkexec apt-get remove` (shows what else apt removes first) | `apt list --upgradable` |
| Flatpak    | `X-Flatpak=` key / `flatpak/exports` path       | `flatpak uninstall` (optionally `--delete-data`) | `flatpak remote-ls --updates` |
| Snap       | `/var/lib/snapd/desktop/applications`           | `pkexec snap remove`               | `snap refresh --list`         |
| AppImage   | `Exec`/`TryExec` points to a `*.AppImage`       | trash the file, entry and icon     | embedded update info or a custom source (see below) |
| Local      | anything else (web apps, hand-made launchers)   | trash the entry (user dir only)    | —                             |

### AppImages

- **Open** (Ctrl+O) or drop an `.AppImage` on the window to install it: it is moved to
  `~/AppImages/<name>.appimage`, its icon goes to `~/AppImages/.icons/`, and a menu entry is
  written to `~/.local/share/applications/<name>.desktop`.
- Metadata is read with `7z`, which never runs code from the AppImage.
- Update sources: the AppImage's built-in `.upd_info` (`gh-releases-zsync`, `zsync`), or a custom
  **GitHub Releases** / **Direct URL** source set on the app's page, stored in
  `~/.config/app-commander/appimage-updates.json`.
- Updates download beside the old file and replace it atomically; a download that isn't an
  ELF/AppImage is rejected and the installed version kept.

### Other features

- **Start at Login** switch per app (uses `~/.config/autostart`; an app's own autostart file, with
  options like `--minimized`, is kept and only switched on or off).
- **New Launcher…** (Ctrl+N) creates a menu entry for any program, script or AppImage; its page
  opens right away so you can set the icon, keywords and categories.
- **Live output** for updates and uninstalls, with a Cancel button for Flatpak operations and
  AppImage downloads (apt and snap aren't cancellable: interrupting them can break the system).
- **Sort** by name, size (largest first) or most recently installed/updated; each row shows how
  long ago the app was installed or last updated.
- **Broken entries**: launchers whose program no longer exists get a *Broken* badge and filter,
  with a one-click *Remove Entry* for the ones in your home folder.

- Filter by source, search (Ctrl+F), show hidden entries (Ctrl+H), reload (Ctrl+R).
- Shows overridden entries (e.g. a `~/.local/share/applications` copy shadowing `/usr/share/applications`).
- **Edit everything in place** on an app's page, each field saved on its own: click the icon to pick
  from every icon in your theme (or an image, copied to `~/.local/share/app-commander/icons/`); name,
  description, generic name and command save on Enter/✓; keywords and categories are tag bubbles
  (Enter or comma adds, × removes, + offers standard categories); *Run in Terminal* is a switch.
  Only the changed key is written, and the translation for your current language too, so the new
  name is what you see.
- **Show in App Menu** toggle and **Edit**: system entries are copied to
  `~/.local/share/applications` first (marked `X-AppCommander-Override=true`), so
  **Reset Entry to Default** can remove the copy later.
- `apt` update checks use the existing package cache. *Refresh Package Lists…*
  in the menu runs `apt-get update` (asks for your password).

## Run

Needs the distro's PyGObject (`python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`, libadwaita ≥ 1.7) and
`7zip` for installing AppImages.
The launcher uses `/usr/bin/python3` explicitly so a conda/venv Python on `PATH` doesn't break it.

```sh
./app-commander          # run from the checkout
./install.sh             # add it to the app menu (./install.sh --uninstall to remove)
/usr/bin/python3 -m unittest discover -s tests -t .
```

## Debian package

```sh
./build-deb.sh                                    # -> dist/app-commander_<version>_all.deb
sudo apt install ./dist/app-commander_0.2.0_all.deb
```

The package installs the code to `/usr/share/app-commander`, a launcher at `/usr/bin/app-commander`,
plus the desktop entry, icon and AppStream metadata. It depends on `python3-gi`, `gir1.2-gtk-4.0`,
`gir1.2-adw-1 (>= 1.7)`, `7zip` and `pkexec` (Debian 13 / Ubuntu 25.04 or newer). The version comes
from `VERSION` in `appcommander/main.py`; packaging files live in `packaging/`.

