import os

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from . import appimage, backends, scanner
from .app_details import AppDetails
from .app_list import AppList
from .install_page import InstallPage
from .utils import run_async


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(title='App Commander', default_width=760, default_height=780, **kwargs)
        self.apps: list = []
        self.appimage_checks: dict = {}  # appimage path -> UpdateCheck, survives reloads
        self.update_keys: set = set()  # survives reloads after an update check
        self.details: AppDetails = None

        self._extend_icon_search_path()

        # list page
        header = Adw.HeaderBar()
        self.refresh_btn = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Reload (Ctrl+R)',
                                      action_name='win.reload')
        open_btn = Gtk.Button(child=Adw.ButtonContent(icon_name='list-add-symbolic', label='Open'),
                              tooltip_text='Install an AppImage (Ctrl+O), or drop one on the window',
                              action_name='win.open-appimage')
        header.pack_start(open_btn)
        header.pack_start(self.refresh_btn)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append('Show Hidden Entries', 'win.show-hidden')
        section.append('Refresh Package Lists…', 'win.refresh-apt')
        menu.append_section(None, section)
        about = Gio.Menu()
        about.append('About App Commander', 'app.about')
        menu.append_section(None, about)
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, primary=True,
                                       tooltip_text='Main Menu'))
        self.search_btn = Gtk.ToggleButton(icon_name='system-search-symbolic', tooltip_text='Search (Ctrl+F)')
        header.pack_end(self.search_btn)

        self.app_list = AppList()
        self.app_list.connect('selected-app', self._on_selected_app)
        self.app_list.connect('check-updates', lambda *_: self.check_updates())
        self.app_list.connect('update-all', self._on_update_all)
        self.search_btn.bind_property('active', self.app_list.search_bar, 'search-mode-enabled',
                                      GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.app_list.search_bar.set_key_capture_widget(self)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        loading.append(Adw.Spinner(width_request=48, height_request=48))
        loading.append(Gtk.Label(label='Scanning applications…', css_classes=['dim-label']))
        self.stack.add_named(loading, 'loading')
        self.stack.add_named(self.app_list, 'list')

        list_toolbar = Adw.ToolbarView(content=self.stack)
        list_toolbar.add_top_bar(header)

        self.nav = Adw.NavigationView()
        self.nav.add(Adw.NavigationPage(child=list_toolbar, title='App Commander', tag='list'))
        self.nav.connect('popped', self._on_page_popped)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('drop', self._on_drop)
        self.nav.add_controller(drop)

        self.toast_overlay = Adw.ToastOverlay(child=self.nav)
        self.set_content(self.toast_overlay)

        self._install_actions()
        self.reload()

    # ------------------------------------------------------------ setup

    def _extend_icon_search_path(self):
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for d in scanner.data_dirs():
            icons = os.path.join(d, 'icons')
            if os.path.isdir(icons) and icons not in theme.get_search_path():
                theme.add_search_path(icons)

    def _install_actions(self):
        def add(name, cb, accels=()):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda *_: cb())
            self.add_action(action)
            if accels:
                self.get_application().set_accels_for_action(f'win.{name}', list(accels))

        add('reload', self.reload, ['<Control>r', 'F5'])
        add('open-appimage', self._on_open_appimage, ['<Control>o'])
        add('search', lambda: self.search_btn.set_active(not self.search_btn.get_active()), ['<Control>f'])
        add('refresh-apt', lambda: self.check_updates(refresh_apt=True))

        show_hidden = Gio.SimpleAction.new_stateful('show-hidden', None, GLib.Variant.new_boolean(False))
        show_hidden.connect('change-state', self._on_show_hidden)
        self.add_action(show_hidden)
        self.get_application().set_accels_for_action('win.show-hidden', ['<Control>h'])

    def _on_show_hidden(self, action, value):
        action.set_state(value)
        self.app_list.set_show_hidden(value.get_boolean())

    # ------------------------------------------------------------ helpers used by pages

    def toast(self, text: str):
        if not text:
            return
        # replace the previous toast instead of queueing, so quick successive edits don't pile up
        if getattr(self, '_last_toast', None):
            self._last_toast.dismiss()
        self._last_toast = Adw.Toast(title=GLib.markup_escape_text(text), timeout=3)
        self.toast_overlay.add_toast(self._last_toast)

    def show_log(self, heading: str, log: str):
        view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            css_classes=['log-view'], top_margin=6, bottom_margin=6, left_margin=6,
                            right_margin=6)
        view.get_buffer().set_text(log.strip())
        scrolled = Gtk.ScrolledWindow(child=view, min_content_height=240, min_content_width=480,
                                      css_classes=['card'])
        dialog = Adw.AlertDialog(heading=heading, extra_child=scrolled)
        dialog.add_response('close', 'Close')
        dialog.present(self)

    # ------------------------------------------------------------ loading

    @staticmethod
    def _update_key(app):
        return (app.backend, app.package, app.installation, app.appimage_path)

    def reload(self):
        if not self.apps:
            self.stack.set_visible_child_name('loading')
        self.refresh_btn.set_sensitive(False)

        def work():
            apps = scanner.scan()
            backends.load_metadata(apps)
            return apps

        run_async(work, self._on_loaded)

    def _on_loaded(self, result, error):
        self.refresh_btn.set_sensitive(True)
        if error:
            self.toast(f'Scan failed: {error}')
            return
        self.apps = result
        for app in self.apps:
            app.update_available = self._update_key(app) in self.update_keys
            if app.appimage_path in self.appimage_checks:
                app.extra['update_check'] = self.appimage_checks[app.appimage_path]
        self.app_list.set_apps(self.apps)
        if self.update_keys:
            self.app_list.set_updates_state(('done', sum(a.update_available for a in self.apps)))
        self.stack.set_visible_child_name('list')
        if self.details:
            match = next((a for a in self.apps if a.desktop_id == self.details.app.desktop_id), None)
            if match:
                self.details.refresh(match, self.apps)

    # ------------------------------------------------------------ updates

    def check_updates(self, refresh_apt: bool = False):
        self.app_list.set_updates_state('checking')
        run_async(backends.check_updates, self._on_updates_checked, self.apps, refresh_apt)

    def _on_updates_checked(self, errors, error):
        self.update_keys = {self._update_key(a) for a in self.apps if a.update_available}
        self.appimage_checks = {a.appimage_path: a.extra['update_check'] for a in self.apps
                                if 'update_check' in a.extra}
        self.app_list.set_updates_state(('done', len(self.update_keys)))
        failed = list((errors or {}).keys()) + (['all'] if error else [])
        if failed:
            self.toast('Update check failed for: ' + ', '.join(scanner.BACKEND_LABELS.get(f, f) for f in failed))

    def _on_update_all(self, *_):
        targets = {}
        for a in self.apps:
            if a.update_available:
                targets.setdefault(self._update_key(a), a)
        targets = list(targets.values())
        if not targets:
            return
        names = '\n'.join(f'• {a.package or a.name}  ({scanner.BACKEND_LABELS[a.backend]})' for a in targets)
        dialog = Adw.AlertDialog(heading=f'Update {len(targets)} application{"s" if len(targets) > 1 else ""}?',
                                 body=names, close_response='cancel', default_response='update')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('update', 'Update All')
        dialog.set_response_appearance('update', Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, response):
            if response != 'update':
                return
            self.app_list.update_all_btn.set_sensitive(False)
            self.app_list.update_all_btn.set_label('Updating…')
            run_async(backends.run_commands, lambda r, e: self._on_update_all_done(targets, r, e),
                      backends.update_command(targets))

        dialog.connect('response', on_response)
        dialog.present(self)

    def _on_update_all_done(self, targets, result, error):
        self.app_list.update_all_btn.set_sensitive(True)
        self.app_list.update_all_btn.set_label('Update all')
        ok, log = result if result else (False, str(error))
        if ok:
            for a in targets:
                self.update_keys.discard(self._update_key(a))
            self.toast('All updates installed')
        else:
            self.show_log('Some updates failed', log)
        self.reload()

    # ------------------------------------------------------------ navigation

    def _on_selected_app(self, _list, app):
        self.search_btn.set_active(False)
        self.details = AppDetails(app, self.apps)
        self.details.connect('changed', self._on_details_changed)
        self.details.connect('uninstalled', self._on_uninstalled)
        self.details.connect('edited', lambda _d, message: (self.toast(message), self.reload()))
        self.nav.push(self.details)

    def _on_details_changed(self, _details, message):
        self.toast(message)
        app = self.details.app
        if not app.update_available:
            self.update_keys.discard(self._update_key(app))
        if 'update_check' not in app.extra:
            self.appimage_checks.pop(app.appimage_path, None)
        self.reload()

    def _on_uninstalled(self, _details, message):
        self.toast(message)
        self.nav.pop()
        self.reload()

    # ------------------------------------------------------------ AppImage installation

    def _on_open_appimage(self):
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter(name='AppImages')
        f.add_pattern('*.AppImage')
        f.add_pattern('*.appimage')
        f.add_mime_type('application/vnd.appimage')
        filters.append(f)
        dialog = Gtk.FileDialog(title='Open an AppImage', filters=filters, default_filter=f)

        def done(d, result):
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            if file:
                self.open_appimage(file.get_path())
        dialog.open(self, None, done)

    def _on_drop(self, _target, value, _x, _y):
        files = value.get_files()
        if files:
            self.open_appimage(files[0].get_path())
        return True

    def open_appimage(self, path: str):
        if not path or not appimage.is_appimage(path):
            self.toast('This file is not an AppImage')
            return
        self.toast_overlay.add_toast(Adw.Toast(title='Reading AppImage…', timeout=1))
        run_async(appimage.inspect, self._on_appimage_inspected, path)

    def _on_appimage_inspected(self, info, error):
        if error:
            self.toast(f'Could not read AppImage: {error}')
            return
        page = InstallPage(info)
        page.connect('installed', self._on_appimage_installed)
        self.nav.pop_to_tag('list')
        self.nav.push(page)

    def _on_appimage_installed(self, _page, _desktop_path, message):
        self.toast(message)
        self.nav.pop_to_tag('list')
        self.reload()

    def _on_page_popped(self, _nav, page):
        if page is self.details:
            self.details = None

