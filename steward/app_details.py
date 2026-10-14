import os
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from . import appimage, backends, entries
from .scanner import App, BACKEND_LABELS, APT, FLATPAK, SNAP, APPIMAGE, LOCAL, user_data_dir
from .icon_picker import IconPicker
from .operation_view import OperationView
from .tags import MAIN_CATEGORIES, TagsGroup, join_tags, split_tags
from .utils import (copy_to_clipboard, icon_image, open_containing_folder, open_in_text_editor,
                    run_async, trash)


def _row(title: str, value: str, mono: bool = False) -> Adw.ActionRow:
    row = Adw.ActionRow(use_markup=False, subtitle_selectable=True, css_classes=['property'])
    # set texts after use_markup, otherwise they are parsed as markup ('&' breaks them)
    row.set_title(title)
    row.set_subtitle(value or '—')
    if mono:
        row.add_css_class('monospace')
    return row


def _icon_button(icon: str, tooltip: str, callback) -> Gtk.Button:
    btn = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER, css_classes=['flat'])
    btn.connect('clicked', lambda *_: callback())
    return btn


class AppDetails(Adw.NavigationPage):
    __gsignals__ = {
        # emitted after an operation changed the system: (toast message)
        'changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'uninstalled': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # emitted after a field of the desktop entry was saved: (toast message)
        'edited': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, app: App, all_apps: list):
        super().__init__(title=app.name)
        self.app = app
        self.all_apps = all_apps
        self.busy = False

        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append('Open in Text Editor', 'details.open-text-editor')
        menu.append('Open Entry Location', 'details.open-entry-folder')
        menu.append('Copy Entry Path', 'details.copy-path')
        header.pack_end(Gtk.MenuButton(icon_name='view-more-symbolic', menu_model=menu,
                                       tooltip_text='More'))
        self._install_actions()

        self.toolbar = Adw.ToolbarView()
        self.toolbar.add_top_bar(header)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=10, margin_bottom=10,
                                margin_start=12, margin_end=12)
        clamp = Adw.Clamp(child=self.main_box, maximum_size=600, margin_top=10, margin_bottom=20)
        self.toolbar.set_content(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(self.toolbar)
        self._build()

    @staticmethod
    def _layout_key(app: App) -> tuple:
        """What the page layout depends on besides the editable fields (which update in place)."""
        return (app.effective.path, len(app.files), app.hidden, app.broken, app.version, app.size,
                app.update_available, id(app.extra.get('update_check')))

    def refresh(self, app: App, all_apps: list):
        """Take a freshly scanned version of the same app; rebuild only if the layout changed, so
        inline edits don't lose focus or scroll position."""
        if self.busy:
            return
        rebuild = self._layout_key(app) != self._layout_key(self.app)
        self.app, self.all_apps = app, all_apps
        self.set_title(app.name)
        if not rebuild:
            return
        adj = self.toolbar.get_content().get_vadjustment()
        scroll = adj.get_value()
        while child := self.main_box.get_first_child():
            self.main_box.remove(child)
        self._build()
        GLib.idle_add(lambda: adj.set_value(scroll) and False)

    # ------------------------------------------------------------ layout

    def _build(self):
        app = self.app
        box = self.main_box

        # the icon is a button: click to change it
        self.icon_slot = Gtk.Box()
        self.icon_slot.append(icon_image(app.icon, 128))
        overlay = Gtk.Overlay(child=self.icon_slot)
        overlay.add_overlay(Gtk.Image(icon_name='document-edit-symbolic', halign=Gtk.Align.END,
                                      valign=Gtk.Align.END, css_classes=['icon-edit-badge']))
        icon_btn = Gtk.Button(child=overlay, css_classes=['flat', 'icon-button'], halign=Gtk.Align.CENTER,
                              tooltip_text='Change icon')
        icon_btn.connect('clicked', self._on_icon_clicked)
        box.append(icon_btn)
        self.title_label = Gtk.Label(label=app.name, css_classes=['title-1'], wrap=True,
                                     justify=Gtk.Justification.CENTER, margin_top=5)
        box.append(self.title_label)
        self.comment_label = Gtk.Label(label=app.comment, css_classes=['dim-label'], wrap=True,
                                       justify=Gtk.Justification.CENTER, margin_top=2, visible=bool(app.comment))
        box.append(self.comment_label)

        pills = Adw.ToggleGroup(can_target=False, css_classes=['round'], halign=Gtk.Align.CENTER,
                                margin_top=20, margin_bottom=10)
        source = BACKEND_LABELS[app.backend]
        if app.installation:
            source += f' ({app.installation})'
        for text in (app.version, app.size, source):
            if text:
                pills.add(Adw.Toggle(label=text))
        pills.set_active(Gtk.INVALID_LIST_POSITION)
        box.append(pills)

        if app.broken:
            box.append(Gtk.Label(
                label=f'This entry is broken: “{app.broken}” is not installed, so it can’t be launched '
                      f'and doesn’t appear in the app menu. It is probably a leftover.',
                wrap=True, justify=Gtk.Justification.CENTER, css_classes=['error'], margin_top=10))

        # action buttons
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=20,
                          halign=Gtk.Align.CENTER)
        self.launch_btn = Gtk.Button(label='Launch', css_classes=['big-btn', 'pill'], width_request=220,
                                     visible=not app.broken)
        self.launch_btn.connect('clicked', self._on_launch)
        self.update_btn = Gtk.Button(label='Update', css_classes=['big-btn', 'pill', 'suggested-action'],
                                     width_request=220,
                                     visible=app.update_available and backends.can_update(app))
        self.update_btn.connect('clicked', self._on_update)
        self.uninstall_btn = Gtk.Button(label=self._uninstall_label(),
                                        css_classes=['big-btn', 'pill', 'destructive-action'],
                                        width_request=220, sensitive=backends.can_uninstall(app))
        if not backends.can_uninstall(app):
            self.uninstall_btn.set_tooltip_text('This entry was installed outside any package manager '
                                                'into a system folder; remove it manually.')
        self.uninstall_btn.connect('clicked', self._on_uninstall)
        for b in (self.launch_btn, self.update_btn, self.uninstall_btn):
            buttons.append(b)
        box.append(buttons)

        self.operation = OperationView(margin_top=20, visible=False)
        box.append(self.operation)

        for group in self._menu_groups():
            box.append(group)
        box.append(self._entry_group())
        box.append(self._package_group())
        if app.backend == APPIMAGE and app.appimage_path:
            box.append(self._update_source_group())

    def _uninstall_label(self) -> str:
        if self.app.backend == LOCAL:
            return 'Remove Entry'
        return 'Uninstall'

    def _entry_group(self) -> Adw.PreferencesGroup:
        app, eff = self.app, self.app.effective
        group = Adw.PreferencesGroup(title='Desktop Entry', margin_top=20)

        loc = _row('Location', eff.path, mono=True)
        loc.add_suffix(_icon_button('folder-open-symbolic', 'Open containing folder',
                                    lambda: open_containing_folder(self.get_root(), eff.path)))
        group.add(loc)

        group.add(_row('Desktop ID', app.desktop_id, mono=True))
        command = self._entry_row('Command', eff.get('Exec'), 'Exec', required=True)
        command.add_css_class('monospace')
        group.add(command)
        terminal = Adw.SwitchRow(title='Run in Terminal', active=eff.get_bool('Terminal'))
        terminal.connect('notify::active', lambda r, _p: self._save(
            {'Terminal': 'true' if r.get_active() else 'false'},
            'Runs in a terminal' if r.get_active() else 'No longer runs in a terminal'))
        group.add(terminal)

        if app.is_overridden:
            exp = Adw.ExpanderRow(title='Overridden Entries',
                                  subtitle=f'This entry takes precedence over {len(app.files) - 1} other '
                                           f'file{"s" if len(app.files) > 2 else ""}')
            for df in app.files[1:]:
                r = _row(df.data_dir, df.path, mono=True)
                r.add_suffix(_icon_button('folder-open-symbolic', 'Open containing folder',
                                          lambda p=df.path: open_containing_folder(self.get_root(), p)))
                exp.add_row(r)
            group.add(exp)


        autostart = Adw.SwitchRow(title='Start at Login', active=entries.is_autostart(app),
                                  subtitle='Launch automatically when you log in')
        autostart.connect('notify::active', self._on_autostart_toggled)
        group.add(autostart)

        self.visible_switch = Adw.SwitchRow(title='Show in App Menu', active=not app.hidden,
                                            subtitle='Hiding writes an override to your local applications folder')
        self.visible_switch.connect('notify::active', self._on_visibility_toggled)
        group.add(self.visible_switch)

        if app.user_override and entries.is_our_override(app.user_override.path):
            reset = Adw.ButtonRow(title='Reset Entry to Default', start_icon_name='edit-undo-symbolic')
            reset.connect('activated', self._on_reset_override)
            group.add(reset)
        return group

    def _menu_groups(self) -> list:
        """Name, description, keywords and categories, each saved as soon as it's changed."""
        eff = self.app.effective
        description = None
        if eff.data_dir != user_data_dir():
            description = ('Changes are saved to a copy in your local applications folder, which '
                           'takes precedence over the packaged entry')
        menu = Adw.PreferencesGroup(title='Menu Entry', description=description, margin_top=30)
        menu.add(self._entry_row('Name', eff.get_localized('Name'), 'Name', required=True))
        menu.add(self._entry_row('Description', eff.get_localized('Comment'), 'Comment'))
        menu.add(self._entry_row('Generic Name (e.g. “Web Browser”)', eff.get_localized('GenericName'),
                                 'GenericName'))

        keywords = TagsGroup('Search', 'Add keywords', split_tags(eff.get_localized('Keywords')),
                             description='Extra words that find this app in the app menu search', margin_top=20)
        keywords.connect('changed', lambda g: self._save({'Keywords': join_tags(g.tags)}, 'Keywords saved'))
        categories = TagsGroup('Categories', 'Add categories', split_tags(eff.get('Categories')),
                               suggestions=MAIN_CATEGORIES, margin_top=20,
                               description='Decide which folder or section the app appears in')
        categories.connect('changed', lambda g: self._save({'Categories': join_tags(g.tags)}, 'Categories saved'))
        return [menu, keywords, categories]

    def _entry_row(self, title: str, value: str, key: str, required: bool = False) -> Adw.EntryRow:
        """An entry row that saves its key when applied (Enter or the ✓ button)."""
        row = Adw.EntryRow(title=title, text=value, show_apply_button=True)
        row.saved_value = value

        def apply(r):
            text = r.get_text().strip()
            if required and not text:
                self._toast(f'{title} can’t be empty')
                r.set_text(r.saved_value)
                return
            if text != r.saved_value and self._save({key: text}, f'{title.split(" (")[0]} saved'):
                r.saved_value = text
                if key == 'Name':
                    self.title_label.set_label(text)
                    self.set_title(text)
                elif key == 'Comment':
                    self.comment_label.set_label(text)
                    self.comment_label.set_visible(bool(text))
        row.connect('apply', apply)
        return row

    def _save(self, edits: dict, message: str) -> bool:
        try:
            if os.path.isabs(edits.get('Icon', '')):
                # keep a private copy of picked images, so the entry survives the file being moved
                edits['Icon'] = entries.store_icon(self.app, edits['Icon'])
            entries.apply_edits(self.app, edits)
        except OSError as e:
            self.get_root().show_log('Could not save the entry', str(e))
            return False
        self.emit('edited', message)
        return True

    def _on_icon_clicked(self, *_):
        picker = IconPicker(current=self.app.icon)

        def chosen(_picker, icon):
            if self._save({'Icon': icon}, 'Icon changed'):
                self.icon_slot.remove(self.icon_slot.get_first_child())
                self.icon_slot.append(icon_image(icon, 128))
        picker.connect('chosen', chosen)
        picker.present(self.get_root())

    def _package_group(self) -> Adw.PreferencesGroup:
        app = self.app
        group = Adw.PreferencesGroup(title='Package', margin_top=20)
        source = BACKEND_LABELS[app.backend]
        if app.installation:
            source += f' ({app.installation} installation)'
        if app.backend == LOCAL:
            source += ' — not managed by a package manager'
        group.add(_row('Source', source))

        if app.package:
            title = {APT: 'Debian Package', FLATPAK: 'Application ID', SNAP: 'Snap Name'}[app.backend]
            group.add(_row(title, app.package, mono=True))
        if app.origin:
            group.add(_row('Origin' if app.backend == FLATPAK else 'Channel', app.origin))

        if app.backend == APPIMAGE:
            r = _row('AppImage File', app.appimage_path, mono=True)
            if app.extra.get('missing'):
                r.set_title('AppImage File (missing!)')
            r.add_suffix(_icon_button('folder-open-symbolic', 'Open containing folder',
                                      lambda: open_containing_folder(self.get_root(), app.appimage_path)))
            group.add(r)

        siblings = self._siblings()
        if siblings:
            group.add(_row('Also Provides', ', '.join(sorted(a.name for a in siblings))))
        return group

    def _update_source_group(self) -> Adw.PreferencesGroup:
        app = self.app
        source, origin = appimage.get_update_source(app.appimage_path)
        group = Adw.PreferencesGroup(title='Updates', margin_top=20)

        subtitle = appimage.describe_source(source)
        if origin == 'embedded':
            subtitle += '\n(built into the AppImage)'
        group.add(_row('Update Source', subtitle))

        check = app.extra.get('update_check')
        if check:
            version = check.extra.get('version')
            if check.error and source:
                status = f'Check failed: {check.error}'
            elif check.available:
                status = 'Update available' + (f': {version}' if version else '')
            elif check.available is False:
                status = 'Up to date' + (f' (latest: {version})' if version else '')
            else:
                status = 'Unknown'
            group.add(_row('Last Check', status))

        # custom source editor
        custom = source if origin == 'custom' else {}
        exp = Adw.ExpanderRow(title='Custom Update Source',
                              subtitle='Overrides the source built into the AppImage' if origin == 'embedded'
                              else 'Where to look for new versions')
        kind_row = Adw.ComboRow(title='Type', model=Gtk.StringList.new(['GitHub Releases', 'Direct Download URL']),
                                selected=1 if custom.get('type') == 'url' else 0)
        repo_row = Adw.EntryRow(title='Repository (owner/name)', text=custom.get('repo', ''))
        pattern_row = Adw.EntryRow(title='Release file name, wildcards allowed (MyApp-*-x86_64.AppImage)',
                                   text=custom.get('pattern', ''))
        pre_row = Adw.SwitchRow(title='Allow Pre-releases', active=custom.get('prereleases', False))
        url_row = Adw.EntryRow(title='Download URL', text=custom.get('url', ''))
        save_row = Adw.ButtonRow(title='Save Update Source')

        def sync_kind(*_):
            github = kind_row.get_selected() == 0
            for r in (repo_row, pattern_row, pre_row):
                r.set_visible(github)
            url_row.set_visible(not github)

        def save(*_):
            if kind_row.get_selected() == 0:
                repo, pattern = repo_row.get_text().strip(), pattern_row.get_text().strip()
                if repo.count('/') != 1 or not pattern:
                    self._toast('Enter the repository as owner/name and a release file name')
                    return
                new = {'type': 'github', 'repo': repo, 'pattern': pattern, 'prereleases': pre_row.get_active()}
            else:
                url = url_row.get_text().strip()
                if not url.startswith(('http://', 'https://')):
                    self._toast('Enter an http(s) URL')
                    return
                new = {'type': 'url', 'url': url}
            appimage.set_custom_source(app.appimage_path, new)
            app.extra.pop('update_check', None)
            self.emit('changed', 'Update source saved')

        def clear(*_):
            appimage.set_custom_source(app.appimage_path, None)
            app.extra.pop('update_check', None)
            self.emit('changed', 'Custom update source removed')

        kind_row.connect('notify::selected', sync_kind)
        save_row.connect('activated', save)
        for r in (kind_row, repo_row, pattern_row, pre_row, url_row, save_row):
            exp.add_row(r)
        if origin == 'custom':
            clear_row = Adw.ButtonRow(title='Remove Custom Source')
            clear_row.connect('activated', clear)
            exp.add_row(clear_row)
        sync_kind()
        group.add(exp)
        return group

    def _siblings(self) -> list:
        app = self.app
        if not app.package:
            return []
        return [a for a in self.all_apps if a is not app and a.backend == app.backend
                and a.package == app.package and a.installation == app.installation]

    # ------------------------------------------------------------ actions

    def _install_actions(self):
        group = Gio.SimpleActionGroup()
        for name, cb in (('open-entry-folder',
                          lambda *_: open_containing_folder(self.get_root(), self.app.effective.path)),
                         ('copy-path', lambda *_: self._copy_path()),
                         ('open-text-editor', lambda *_: self._on_edit_entry())):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', cb)
            group.add_action(action)
        self.insert_action_group('details', group)

    def _copy_path(self):
        copy_to_clipboard(self.app.effective.path)
        self._toast('Path copied')

    def _toast(self, text: str):
        self.get_root().toast(text)

    def _set_busy(self, text: str = None):
        """Short busy state (no output), e.g. while asking apt what a removal would do."""
        if text is None:
            self.operation.finish()
        else:
            self.operation.start(text)
            self.operation.expander.set_visible(False)
        self._lock(text is not None)

    def _start_operation(self, status: str):
        self.operation.expander.set_visible(True)
        op = self.operation.start(status)
        self._lock(True)
        return op

    def _lock(self, busy: bool):
        self.busy = busy
        for b in (self.launch_btn, self.update_btn, self.uninstall_btn):
            b.set_sensitive(not busy and (b is not self.uninstall_btn or backends.can_uninstall(self.app)))
        self.set_can_pop(not busy)

    def _operation_failed(self, what: str, op):
        self._lock(False)
        self.operation.finish('Cancelled' if op.cancelled else f'{what} failed — see the output below',
                              keep=True)
        if not op.cancelled:
            self.operation.show_output()

    def _on_launch(self, *_):
        try:
            # raises TypeError when GLib rejects the entry (e.g. its TryExec program is missing)
            info = Gio.DesktopAppInfo.new_from_filename(self.app.effective.path)
            if not info.launch([], self.get_display().get_app_launch_context()):
                raise RuntimeError('the launch was refused')
        except (TypeError, RuntimeError, GLib.Error) as e:
            reason = getattr(e, 'message', None) or str(e)
            self._toast(f'Could not launch {self.app.name}: {reason}')

    def _on_edit_entry(self):
        path = self.app.effective.path
        if entries.editable_path(self.app) != path:
            path = entries.editable_path(self.app)
            self._toast('Created an editable copy in your local applications folder')
            self.emit('changed', '')
        if not open_in_text_editor(path):
            self._toast('No text editor found')

    def _on_visibility_toggled(self, switch, _pspec):
        hidden = not switch.get_active()
        if hidden == self.app.hidden:
            return
        try:
            entries.set_hidden(self.app, hidden)
        except OSError as e:
            self._toast(f'Could not write entry: {e.strerror}')
            return
        self.emit('changed', 'Hidden from app menu' if hidden else 'Shown in app menu')

    def _on_autostart_toggled(self, switch, _pspec):
        try:
            entries.set_autostart(self.app, switch.get_active())
        except OSError as e:
            self._toast(f'Could not change autostart: {e.strerror}')
            return
        self._toast(f'{self.app.name} will start at login' if switch.get_active()
                    else f'{self.app.name} won’t start at login')

    def _on_reset_override(self, *_):
        trash(self.app.user_override.path)
        self.emit('changed', 'Entry reset to default')
        self.get_root().nav.pop()

    # ------------------------------------------------------------ update

    def _on_update(self, *_):
        op = self._start_operation(f'Updating {self.app.name}…')
        cmds = backends.update_command([self.app])
        run_async(backends.run_commands, lambda r, e: self._on_update_done(op, r, e), cmds, op)

    def _on_update_done(self, op, result, error):
        ok, log = result if result else (False, str(error))
        if not ok:
            if error:
                op.output(str(error))
            self._operation_failed('Update', op)
            return
        self._lock(False)
        self.operation.finish()
        self.app.update_available = False
        self.app.extra.pop('update_check', None)
        self.update_btn.set_visible(False)
        self.emit('changed', f'{self.app.name} was updated')

    # ------------------------------------------------------------ uninstall

    def _on_uninstall(self, *_):
        app = self.app
        if app.backend == APT:
            self._set_busy('Checking dependencies…')
            run_async(backends.simulate_apt_removal, self._confirm_apt_uninstall, app.package)
        else:
            self._confirm_uninstall()

    def _confirm_apt_uninstall(self, removed, error):
        self._set_busy(None)
        others = [p for p in (removed or []) if p != self.app.package]
        self._confirm_uninstall(others)

    def _confirm_uninstall(self, other_packages: list = None):
        app = self.app
        self.delete_data_check = None
        extra = None
        esc = GLib.markup_escape_text

        body = {
            APT: f'The package “{esc(app.package or "")}” will be removed with apt.',
            FLATPAK: f'The Flatpak “{esc(app.package or "")}” ({app.installation}) will be uninstalled.',
            SNAP: f'The snap “{esc(app.package or "")}” will be removed.',
            APPIMAGE: 'The AppImage file, its desktop entry and its icon will be moved to the trash.',
            LOCAL: 'The desktop entry will be moved to the trash.',
        }[app.backend]
        siblings = self._siblings()
        if siblings:
            body += '\n\nThe package also provides: ' + esc(', '.join(sorted(a.name for a in siblings)))
        if other_packages:
            n = len(other_packages)
            body += f'\n\n<b>{n} other package{"s" if n > 1 else ""} will also be removed:</b>'
            extra = self._scroll_text('\n'.join(other_packages), height=min(200, 22 * n + 20))

        if app.backend == FLATPAK:
            # a wrapping label: long app IDs would otherwise make the check button wider than the dialog
            self.delete_data_check = Gtk.CheckButton(child=Gtk.Label(
                label=f'Also delete app data (~/.var/app/{app.package})', wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0))
            extra = self.delete_data_check

        dialog = Adw.AlertDialog(heading=f'Uninstall {app.name}?', body=body, body_use_markup=True,
                                 extra_child=extra, close_response='cancel', default_response='cancel')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('uninstall', self._uninstall_label())
        dialog.set_response_appearance('uninstall', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', self._on_uninstall_response)
        dialog.present(self.get_root())

    def _scroll_text(self, text: str, height: int = 200) -> Gtk.Widget:
        view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            css_classes=['log-view'], top_margin=6, bottom_margin=6,
                            left_margin=6, right_margin=6)
        view.get_buffer().set_text(text)
        return Gtk.ScrolledWindow(child=view, min_content_height=height, css_classes=['card'])

    def _on_uninstall_response(self, _dialog, response):
        if response != 'uninstall':
            return
        app = self.app
        delete_data = bool(self.delete_data_check and self.delete_data_check.get_active())
        cmd = backends.uninstall_command(app, delete_data)
        op = self._start_operation(f'Uninstalling {app.name}…')
        run_async(backends.run_commands, lambda r, e: self._on_uninstall_done(op, r, e), [cmd], op)

    def _on_uninstall_done(self, op, result, error):
        ok, log = result if result else (False, str(error))
        if not ok:
            if error:
                op.output(str(error))
            self._operation_failed('Uninstall', op)
            return
        self._lock(False)
        self.operation.finish()
        # Leftover local overrides would keep a broken launcher in the menu.
        override = self.app.user_override
        if override and override.data_dir == user_data_dir() and entries.is_our_override(override.path):
            trash(override.path)
        self.emit('uninstalled', f'{self.app.name} was uninstalled')
