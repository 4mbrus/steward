import re

from gi.repository import Adw, GObject, Gtk

# Main categories from the freedesktop menu spec; these decide where an app appears in menus.
MAIN_CATEGORIES = ['AudioVideo', 'Audio', 'Video', 'Development', 'Education', 'Game', 'Graphics',
                   'Network', 'Office', 'Science', 'Settings', 'System', 'Utility']


def join_tags(tags: list) -> str:
    return ';'.join(tags) + ';' if tags else ''


def split_tags(text: str) -> list:
    return [t.strip() for t in re.split(r'[;,]', text) if t.strip()]


class TagsGroup(Adw.PreferencesGroup):
    """Boxed list with an entry row that turns text into removable pill-shaped tags."""

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, title: str, placeholder: str, tags: list, suggestions: list = None, **kwargs):
        super().__init__(title=title, **kwargs)
        self.tags: list = []
        self.suggestions = suggestions or []

        self.entry = Adw.EntryRow(title=placeholder)
        self.entry.connect('entry-activated', lambda *_: self._commit_entry())
        self.entry.connect('changed', self._on_entry_changed)
        if self.suggestions:
            self.suggest_btn = Gtk.MenuButton(icon_name='list-add-symbolic', valign=Gtk.Align.CENTER,
                                              css_classes=['flat'], tooltip_text='Add a standard category')
            self.suggest_btn.set_create_popup_func(self._build_suggestions)
            self.entry.add_suffix(self.suggest_btn)
        self.add(self.entry)

        self.flow = Adw.WrapBox(child_spacing=8, line_spacing=8, margin_top=12, margin_bottom=12,
                                margin_start=12, margin_end=12)
        self.chips: dict = {}  # tag -> chip widget
        # a PreferencesRow (not a plain ListBoxRow) so the group puts it inside the card
        self.chips_row = Adw.PreferencesRow(child=self.flow, activatable=False, selectable=False)
        self.add(self.chips_row)

        for tag in tags:
            self.add_tag(tag, notify=False)
        self._sync_visibility()

    # ------------------------------------------------------------ public

    def get_tags(self) -> list:
        """Tags including any text still typed in the entry (so Save doesn't drop it)."""
        pending = [t for t in split_tags(self.entry.get_text()) if not self._has(t)]
        return self.tags + pending

    def add_tag(self, tag: str, notify: bool = True):
        tag = tag.strip()
        if not tag or self._has(tag):
            return
        self.tags.append(tag)
        self.chips[tag] = self._chip(tag)
        self.flow.append(self.chips[tag])
        self._sync_visibility()
        if notify:
            self.emit('changed')

    def remove_tag(self, tag: str):
        if tag not in self.tags:
            return
        self.tags.remove(tag)
        self.flow.remove(self.chips.pop(tag))
        self._sync_visibility()
        self.emit('changed')

    # ------------------------------------------------------------ internals

    def _has(self, tag: str) -> bool:
        return tag.lower() in (t.lower() for t in self.tags)

    def _chip(self, tag: str) -> Gtk.Widget:
        box = Gtk.Box(spacing=2, css_classes=['tag-chip'], halign=Gtk.Align.START)
        box.append(Gtk.Label(label=tag))
        remove = Gtk.Button(icon_name='window-close-symbolic', css_classes=['flat', 'circular'],
                            tooltip_text=f'Remove “{tag}”', valign=Gtk.Align.CENTER)
        remove.connect('clicked', lambda *_: self.remove_tag(tag))
        box.append(remove)
        return box

    def _commit_entry(self):
        for tag in split_tags(self.entry.get_text()):
            self.add_tag(tag)
        self.entry.set_text('')

    def _on_entry_changed(self, entry):
        # typing a comma or semicolon commits what came before it
        text = entry.get_text()
        if text and text[-1] in ',;':
            self._commit_entry()

    def _sync_visibility(self):
        self.chips_row.set_visible(bool(self.tags))

    def _build_suggestions(self, button):
        box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                          min_children_per_line=3, row_spacing=4, column_spacing=4, homogeneous=True,
                          margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        available = [s for s in self.suggestions if not self._has(s)]
        for s in available:
            b = Gtk.Button(label=s, css_classes=['flat'])
            b.connect('clicked', lambda _b, s=s: (self.add_tag(s), button.popdown()))
            box.append(b)
        if not available:
            box.append(Gtk.Label(label='All standard categories added', css_classes=['dim-label'],
                                 margin_top=6, margin_bottom=6))
        button.set_popover(Gtk.Popover(child=box))

