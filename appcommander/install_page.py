import os

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from . import appimage
from .app_details import _row
from .utils import icon_image, run_async


class InstallPage(Adw.NavigationPage):
    """Preview of an AppImage file with Install / Run Once actions (like Gear Lever's file view)."""

    __gsignals__ = {
        'installed': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),  # desktop path, message
    }

    def __init__(self, info: appimage.AppImageInfo):
        super().__init__(title='Install AppImage')
        self.info = info
        self.connect('hidden', lambda *_: self.info.cleanup())

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=10, margin_bottom=10,
                      margin_start=12, margin_end=12)
        clamp = Adw.Clamp(child=box, maximum_size=600, margin_top=10, margin_bottom=20)
        toolbar.set_content(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(toolbar)

        box.append(icon_image(info.icon_path or '', 128))
        box.append(Gtk.Label(label=info.name, css_classes=['title-1'], wrap=True,
                             justify=Gtk.Justification.CENTER, margin_top=5))
        if info.comment:
            box.append(Gtk.Label(label=info.comment, css_classes=['dim-label'], wrap=True,
                                 justify=Gtk.Justification.CENTER, margin_top=2))

        pills = Adw.ToggleGroup(can_target=False, css_classes=['round'], halign=Gtk.Align.CENTER,
                                margin_top=20, margin_bottom=10)
        size = os.path.getsize(info.path)
        for text in (info.version, GLib.format_size(size), 'AppImage'):
            if text:
                pills.add(Adw.Toggle(label=text))
        pills.set_active(Gtk.INVALID_LIST_POSITION)
        box.append(pills)

        replacing = os.path.exists(info.desktop_path) or os.path.exists(info.destination)
        same_file = os.path.realpath(info.path) == os.path.realpath(info.destination)

        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=20,
                          halign=Gtk.Align.CENTER)
        label = 'Reinstall' if same_file else 'Replace Installed Version' if replacing else 'Install'
        self.install_btn = Gtk.Button(label=label, css_classes=['big-btn', 'pill', 'suggested-action'],
                                      width_request=220)
        self.install_btn.connect('clicked', self._on_install)
        self.run_btn = Gtk.Button(label='Run Once', css_classes=['big-btn', 'pill'], width_request=220,
                                  tooltip_text='Run without installing')
        self.run_btn.connect('clicked', self._on_run)
        buttons.append(self.install_btn)
        buttons.append(self.run_btn)
        box.append(buttons)

        self.status_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=20, visible=False)
        self.status_box.append(Adw.Spinner())
        self.status_box.append(Gtk.Label(label='Installing…'))
        box.append(self.status_box)

        group = Adw.PreferencesGroup(title='Installation', margin_top=30)
        group.add(_row('File', info.path, mono=True))
        group.add(_row('Will Be Installed As', info.destination, mono=True))
        group.add(_row('Desktop Entry', info.desktop_path, mono=True))
        if replacing and not same_file:
            group.add(_row('Note', f'An app named “{info.slug}” is already installed and will be replaced.'))
        self.keep_row = Adw.SwitchRow(title='Keep Original File',
                                      subtitle='Copy instead of moving the file into ~/AppImages',
                                      visible=not same_file)
        group.add(self.keep_row)
        box.append(group)

        upd = Adw.PreferencesGroup(title='Updates', margin_top=20)
        embedded = appimage.parse_embedded(info.update_info) if info.update_info else None
        upd.add(_row('Update Source', appimage.describe_source(embedded) if embedded else
                     'None built in. You can add one from the app page after installing.'))
        box.append(upd)

    def _on_install(self, *_):
        self.install_btn.set_sensitive(False)
        self.run_btn.set_sensitive(False)
        self.status_box.set_visible(True)
        self.set_can_pop(False)
        run_async(appimage.integrate, self._on_installed, self.info, self.keep_row.get_active())

    def _on_installed(self, desktop_path, error):
        self.status_box.set_visible(False)
        self.set_can_pop(True)
        if error:
            self.install_btn.set_sensitive(True)
            self.run_btn.set_sensitive(True)
            self.get_root().show_log('Installation failed', str(error))
            return
        self.emit('installed', desktop_path, f'{self.info.name} was installed')

    def _on_run(self, *_):
        try:
            if not os.access(self.info.path, os.X_OK):
                os.chmod(self.info.path, os.stat(self.info.path).st_mode | 0o111)
            Gio.Subprocess.new([self.info.path], Gio.SubprocessFlags.NONE)
        except (OSError, GLib.Error) as e:
            self.get_root().toast(f'Could not run: {getattr(e, "message", None) or e}')
