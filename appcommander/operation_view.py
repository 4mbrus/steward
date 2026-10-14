from gi.repository import Adw, GLib, Gtk, Pango

from .backends import Operation

MAX_LOG_LINES = 2000


class OperationView(Gtk.Box):
    """Spinner, status line, cancel button and an expandable live log for a running Operation."""

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, **kwargs)
        self.op = None

        top = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        self.spinner = Adw.Spinner()
        self.status = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=48)
        self.cancel_btn = Gtk.Button(label='Cancel', css_classes=['flat'], visible=False)
        self.cancel_btn.connect('clicked', self._on_cancel)
        for w in (self.spinner, self.status, self.cancel_btn):
            top.append(w)
        self.append(top)

        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, css_classes=['log-view'],
                                 top_margin=6, bottom_margin=6, left_margin=8, right_margin=8)
        self.scroller = Gtk.ScrolledWindow(child=self.view, min_content_height=180, css_classes=['card'])
        self.expander = Gtk.Expander(label='Show Output', child=self.scroller)
        self.expander.connect('notify::expanded', lambda e, _p: e.set_label(
            'Hide Output' if e.get_expanded() else 'Show Output'))
        self.append(self.expander)

    def start(self, status: str) -> Operation:
        """Reset the view and return a new Operation wired to it (thread-safe callbacks)."""
        self.view.get_buffer().set_text('')
        self.status.set_label(status)
        self.spinner.set_visible(True)
        self.cancel_btn.set_visible(False)
        self.cancel_btn.set_sensitive(True)
        self.set_visible(True)
        self.op = Operation(
            on_output=lambda line: GLib.idle_add(self._append, line),
            on_status=lambda text: GLib.idle_add(self.status.set_label, text),
            on_cancellable=lambda c: GLib.idle_add(self.cancel_btn.set_visible, c),
        )
        return self.op

    def finish(self, status: str = None, keep: bool = False):
        """Stop the spinner. With keep=True the view stays visible to show the final status."""
        self.spinner.set_visible(False)
        self.cancel_btn.set_visible(False)
        if status:
            self.status.set_label(status)
        self.set_visible(keep)

    def show_output(self):
        self.expander.set_expanded(True)

    def _append(self, line: str):
        buf = self.view.get_buffer()
        buf.insert(buf.get_end_iter(), ('\n' if buf.get_char_count() else '') + line)
        if buf.get_line_count() > MAX_LOG_LINES:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(buf.get_line_count() - MAX_LOG_LINES)[1])
        adj = self.scroller.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) and False)
        return False

    def _on_cancel(self, *_):
        if self.op:
            self.cancel_btn.set_sensitive(False)
            self.status.set_label('Cancelling…')
            self.op.cancel()
