from gi.repository import Adw, GObject, Gtk, Pango

from .scanner import App, BACKEND_LABELS, APT, FLATPAK, SNAP, APPIMAGE, LOCAL
from .utils import icon_image

CLAMP_SIZE = 640
FILTERS = [('all', 'All'), (APT, 'System'), (FLATPAK, 'Flatpak'), (SNAP, 'Snap'),
           (APPIMAGE, 'AppImage'), (LOCAL, 'Local')]


def source_badge(app: App) -> Gtk.Label:
    label = BACKEND_LABELS[app.backend]
    if app.backend == FLATPAK and app.installation:
        label += f' · {app.installation}'
    return Gtk.Label(label=label, css_classes=['source-badge', app.backend], valign=Gtk.Align.CENTER)


class AppRow(Gtk.ListBoxRow):
    def __init__(self, app: App):
        super().__init__(activatable=True, selectable=False)
        self.app = app

        box = Gtk.Box(spacing=10, css_classes=['app-listbox-item'])
        box.append(icon_image(app.icon, 45))

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        details.append(Gtk.Label(label=app.name, halign=Gtk.Align.START, css_classes=['heading'],
                                 ellipsize=Pango.EllipsizeMode.END))
        if app.comment:
            details.append(Gtk.Label(label=app.comment, halign=Gtk.Align.START, lines=1,
                                     ellipsize=Pango.EllipsizeMode.END))

        meta = Gtk.Box(spacing=6, margin_top=3)
        meta.append(source_badge(app))
        info = [x for x in (app.version, app.size) if x]
        if info:
            meta.append(Gtk.Label(label=' · '.join(info), css_classes=['dim-label', 'caption'],
                                  ellipsize=Pango.EllipsizeMode.END))
        if app.broken:
            meta.append(Gtk.Label(label='Broken', css_classes=['source-badge', 'broken'],
                                  tooltip_text=f'Missing program: {app.broken}'))
            self.add_css_class('hidden-app')
        elif app.hidden:
            meta.append(Gtk.Label(label='Hidden from menu', css_classes=['dim-label', 'caption']))
            self.add_css_class('hidden-app')
        details.append(meta)
        box.append(details)

        self.update_icon = Gtk.Image(icon_name='software-update-available-symbolic',
                                     css_classes=['update-icon'], tooltip_text='Update available',
                                     visible=app.update_available)
        box.append(self.update_icon)
        box.append(Gtk.Image(icon_name='go-next-symbolic', css_classes=['dim-label']))
        self.set_child(box)


class AppList(Gtk.Box):
    """List page content: title row, source filter and boxed list of apps."""

    __gsignals__ = {
        'selected-app': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'check-updates': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'update-all': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.apps: list = []
        self.rows: list = []
        self.query = ''
        self.show_hidden = False

        # search
        self.search_entry = Gtk.SearchEntry(placeholder_text='Filter installed applications', hexpand=True)
        self.search_entry.connect('search-changed', self._on_search_changed)
        self.search_bar = Gtk.SearchBar(child=Adw.Clamp(child=self.search_entry, maximum_size=CLAMP_SIZE))
        self.search_bar.connect_entry(self.search_entry)
        self.append(self.search_bar)

        # title row
        title_row = Gtk.Box(spacing=10, margin_bottom=15)
        self.title = Gtk.Label(label='Installed applications', css_classes=['title-2'],
                               hexpand=True, halign=Gtk.Align.START)
        title_row.append(self.title)
        self.updates_btn = Gtk.Button(label='Check for updates')
        self.updates_btn.connect('clicked', lambda *_: self.emit('check-updates'))
        self.update_all_btn = Gtk.Button(label='Update all', css_classes=['suggested-action'], visible=False)
        self.update_all_btn.connect('clicked', lambda *_: self.emit('update-all'))
        title_row.append(self.updates_btn)
        title_row.append(self.update_all_btn)

        # source filter
        self.filter_group = Adw.ToggleGroup(css_classes=['round'], halign=Gtk.Align.START, margin_bottom=15)
        self.filter_group.connect('notify::active-name', lambda *_: self._refilter())
        filter_scroll = Gtk.ScrolledWindow(child=self.filter_group, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           propagate_natural_height=True)

        self.listbox = Gtk.ListBox(css_classes=['boxed-list'], valign=Gtk.Align.START)
        self.listbox.connect('row-activated', lambda _lb, row: self.emit('selected-app', row.app))
        self.listbox.set_filter_func(self._filter_row)

        self.no_results = Adw.StatusPage(icon_name='edit-find-symbolic', title='No applications found',
                                         visible=False, css_classes=['compact'])

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for w in (title_row, filter_scroll, self.listbox, self.no_results):
            main_box.append(w)

        clamp = Adw.Clamp(child=main_box, maximum_size=CLAMP_SIZE, margin_top=20, margin_bottom=20,
                          margin_start=12, margin_end=12)
        scrolled = Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(scrolled)

    # ------------------------------------------------------------ data

    def set_apps(self, apps: list):
        self.apps = apps
        self.listbox.remove_all()
        self.rows = []
        for app in apps:
            row = AppRow(app)
            self.rows.append(row)
            self.listbox.append(row)
        self._rebuild_filters()
        self._refilter()
        self.set_updates_state(None)

    def _rebuild_filters(self):
        active = self.filter_group.get_active_name() or 'all'
        self.filter_group.remove_all()
        present = {a.backend for a in self.apps}
        names = []
        for name, label in FILTERS:
            if name == 'all' or name in present:
                count = len(self.apps) if name == 'all' else sum(a.backend == name for a in self.apps)
                self.filter_group.add(Adw.Toggle(name=name, label=label, tooltip=f'{count} entries'))
                names.append(name)
        n_broken = sum(1 for a in self.apps if a.broken)
        if n_broken:
            self.filter_group.add(Adw.Toggle(name='broken', label=f'Broken ({n_broken})',
                                             tooltip='Entries whose program is no longer installed'))
            names.append('broken')
        n_updates = len({(a.backend, a.package, a.installation, a.appimage_path)
                         for a in self.apps if a.update_available})
        if n_updates:
            self.filter_group.add(Adw.Toggle(name='updates', label=f'Updates ({n_updates})'))
            names.append('updates')
        self.filter_group.set_active_name(active if active in names else 'all')

    def set_show_hidden(self, show: bool):
        self.show_hidden = show
        self._refilter()

    def toggle_search(self):
        self.search_bar.set_search_mode(not self.search_bar.get_search_mode())

    # ------------------------------------------------------------ updates

    def set_updates_state(self, state):
        """state: None (idle), 'checking', or ('done', n_updates)."""
        self.updates_btn.set_sensitive(state != 'checking')
        if state is None:
            self.updates_btn.set_label('Check for updates')
            self.update_all_btn.set_visible(False)
        elif state == 'checking':
            self.updates_btn.set_label('Checking updates…')
        else:
            n = state[1]
            self.updates_btn.set_label('No updates found' if n == 0 else 'Check again')
            self.update_all_btn.set_visible(n > 0)
            for row in self.rows:
                row.update_icon.set_visible(row.app.update_available)
            self._rebuild_filters()

    # ------------------------------------------------------------ filtering

    def _on_search_changed(self, entry):
        self.query = entry.get_text().strip().lower()
        self._refilter()

    def _filter_row(self, row: AppRow) -> bool:
        app = row.app
        active = self.filter_group.get_active_name() or 'all'
        if active == 'updates':
            return app.update_available and (not self.query or self.query in app.search_text())
        if active == 'broken':
            return bool(app.broken) and (not self.query or self.query in app.search_text())
        if (app.hidden or app.broken) and not self.show_hidden and not self.query:
            return False
        if active not in ('all', 'updates') and app.backend != active:
            return False
        return not self.query or self.query in app.search_text()

    def _refilter(self):
        self.listbox.invalidate_filter()
        visible = sum(1 for r in self.rows if self._filter_row(r))
        self.listbox.set_visible(visible > 0)
        self.no_results.set_visible(visible == 0)
        self.title.set_label(f'Installed applications ({visible})')
