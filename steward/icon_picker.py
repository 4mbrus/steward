from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 24, 128, 48
PREVIEW_SIZE = 128


class IconPicker(Adw.Dialog):
    """Searchable grid of every icon in the current icon theme, plus 'choose an image file'.

    Emits 'chosen' with a theme icon name or an image path.
    """

    __gsignals__ = {
        'chosen': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, current: str = '', query: str = ''):
        super().__init__(title='Choose Icon', content_width=760, content_height=720)
        self.icon_size = DEFAULT_SIZE
        self.show_names = True
        self.selected_name = None

        # header: open file (start), search toggle (end), select (end)
        header = Adw.HeaderBar()
        open_btn = Gtk.Button(icon_name='document-open-symbolic', tooltip_text='Use an image file…')
        open_btn.connect('clicked', self._on_open_file)
        header.pack_start(open_btn)
        self.select_btn = Gtk.Button(label='Select', css_classes=['suggested-action'], sensitive=False)
        self.select_btn.connect('clicked', lambda *_: self._choose(self.selected_name))
        header.pack_end(self.select_btn)

        # preview
        self.preview = Gtk.Image(pixel_size=PREVIEW_SIZE, css_classes=['icon-dropshadow'],
                                 margin_top=12, margin_bottom=12)
        self.preview_label = Gtk.Label(css_classes=['dim-label'], selectable=True,
                                       ellipsize=Pango.EllipsizeMode.MIDDLE, margin_bottom=12)
        self._set_preview(current)

        # search
        self.search = Gtk.SearchEntry(placeholder_text='Search icons (e.g. video, music, terminal)',
                                      text=query, hexpand=True, margin_start=12, margin_end=12)
        self.search.connect('search-changed', self._on_search_changed)
        self.search.connect('activate', self._on_search_activate)

        # model: all theme icon names, full-colour icons before symbolic ones
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        names = sorted(set(theme.get_icon_names()), key=lambda n: (n.endswith('-symbolic'), n.lower()))
        self.store = Gtk.StringList.new(names)
        expression = Gtk.PropertyExpression.new(Gtk.StringObject, None, 'string')
        self.filter = Gtk.StringFilter(expression=expression, ignore_case=True,
                                       match_mode=Gtk.StringFilterMatchMode.SUBSTRING)
        filtered = Gtk.FilterListModel(model=self.store, filter=self.filter, incremental=True)
        self.selection = Gtk.SingleSelection(model=filtered, autoselect=False, can_unselect=True)
        self.selection.connect('notify::selected-item', self._on_selected)
        filtered.connect('items-changed', lambda *_: self._update_empty(filtered))

        self.grid = Gtk.GridView(model=self.selection, max_columns=12, min_columns=1,
                                 css_classes=['icon-grid'])
        self.grid.connect('activate', self._on_activate)
        self._apply_factory()
        scrolled = Gtk.ScrolledWindow(child=self.grid, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.empty = Adw.StatusPage(icon_name='edit-find-symbolic', title='No Icons Found',
                                    description='Try another word, or use an image file', visible=False,
                                    vexpand=True, css_classes=['compact'])
        grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, margin_top=12)
        grid_box.append(scrolled)
        grid_box.append(self.empty)
        self.grid_scrolled = scrolled

        # footer: zoom and names toggle
        footer = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        zoom_out = Gtk.Button(icon_name='zoom-out-symbolic', css_classes=['flat'], tooltip_text='Smaller')
        zoom_in = Gtk.Button(icon_name='zoom-in-symbolic', css_classes=['flat'], tooltip_text='Larger')
        self.zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, MIN_SIZE, MAX_SIZE, 8)
        self.zoom.set_value(self.icon_size)
        self.zoom.set_size_request(140, -1)
        self.zoom.set_draw_value(False)
        self.zoom.connect('value-changed', self._on_zoom)
        zoom_out.connect('clicked', lambda *_: self.zoom.set_value(self.zoom.get_value() - 16))
        zoom_in.connect('clicked', lambda *_: self.zoom.set_value(self.zoom.get_value() + 16))
        names_toggle = Gtk.ToggleButton(label='Show Icon Names', active=True, halign=Gtk.Align.END,
                                        hexpand=True)
        names_toggle.connect('toggled', self._on_names_toggled)
        self.count_label = Gtk.Label(css_classes=['dim-label', 'caption'], margin_start=12)
        for w in (zoom_out, self.zoom, zoom_in, self.count_label, names_toggle):
            footer.append(w)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.preview)
        content.append(self.preview_label)
        content.append(self.search)
        content.append(grid_box)

        toolbar = Adw.ToolbarView(content=content)
        toolbar.add_top_bar(header)
        toolbar.add_bottom_bar(footer)
        self.set_child(toolbar)
        self.set_focus(self.search)
        self._on_search_changed(self.search)

    # ------------------------------------------------------------ grid items

    def _apply_factory(self):
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._setup_item)
        factory.connect('bind', self._bind_item)
        self.grid.set_factory(factory)
        self.grid.set_max_columns(3 if self.show_names else 12)

    def _setup_item(self, _factory, item):
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        if not self.show_names:
            box.set_halign(Gtk.Align.CENTER)
        box.append(Gtk.Image(pixel_size=self.icon_size))
        box.append(Gtk.Label(ellipsize=Pango.EllipsizeMode.END, xalign=0, hexpand=True,
                             visible=self.show_names))
        item.set_child(box)

    def _bind_item(self, _factory, item):
        name = item.get_item().get_string()
        box = item.get_child()
        box.get_first_child().set_from_icon_name(name)
        box.get_last_child().set_label(name)
        box.set_tooltip_text(name)

    # ------------------------------------------------------------ events

    def _on_search_changed(self, entry):
        self.filter.set_search(entry.get_text().strip())
        self.grid_scrolled.get_vadjustment().set_value(0)

    def _on_search_activate(self, _entry):
        # Enter picks the first match
        if self.selection.get_n_items():
            self._choose(self.selection.get_item(0).get_string())

    def _update_empty(self, model):
        n = model.get_n_items()
        self.empty.set_visible(n == 0)
        self.grid_scrolled.set_visible(n > 0)
        self.count_label.set_label(f'{n:,} icons')

    def _on_selected(self, selection, _pspec):
        item = selection.get_selected_item()
        self.selected_name = item.get_string() if item else None
        self.select_btn.set_sensitive(self.selected_name is not None)
        if self.selected_name:
            self._set_preview(self.selected_name)

    def _on_activate(self, _grid, position):
        self._choose(self.selection.get_item(position).get_string())

    def _on_zoom(self, scale):
        size = int(scale.get_value())
        if size != self.icon_size:
            self.icon_size = size
            self._apply_factory()

    def _on_names_toggled(self, button):
        self.show_names = button.get_active()
        self._apply_factory()

    def _set_preview(self, icon: str):
        if icon and icon.startswith('/'):
            self.preview.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(icon)))
        else:
            self.preview.set_from_icon_name(icon or 'application-x-executable')
        self.preview_label.set_label(icon or '')

    def _on_open_file(self, *_):
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter(name='Images')
        for mime in ('image/png', 'image/svg+xml', 'image/x-xpixmap', 'image/jpeg'):
            f.add_mime_type(mime)
        filters.append(f)
        dialog = Gtk.FileDialog(title='Choose an Icon Image', filters=filters, default_filter=f)

        def done(d, result):
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            if file and file.get_path():
                self._choose(file.get_path())
        dialog.open(self.get_root(), None, done)

    def _choose(self, icon: str):
        if icon:
            self.emit('chosen', icon)
            self.close()
