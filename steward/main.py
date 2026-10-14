import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from .utils import migrate_legacy_config  # noqa: E402

APP_ID = 'io.github._4mbrus.Steward'
VERSION = '0.3.0'


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        migrate_legacy_config()
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(os.path.dirname(__file__), 'style.css'))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # the app icon ships in data/icons, so it works when run from the checkout too
        icons = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'icons')
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(icons)
        Gtk.Window.set_default_icon_name(APP_ID)

        for name, cb, accels in (('quit', lambda *_: self.quit(), ['<Control>q']),
                                 ('about', self._on_about, [])):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', cb)
            self.add_action(action)
            self.set_accels_for_action(f'app.{name}', accels)

    def do_activate(self):
        from .window import MainWindow
        win = self.props.active_window or MainWindow(application=self)
        win.present()

    def _on_about(self, *_):
        Adw.AboutDialog(application_name='Steward', application_icon=APP_ID,
                        version=VERSION,
                        comments='See every installed application, where its desktop entry lives, '
                                 'and uninstall or update it — whatever package format it came from.',
                        website='https://github.com/4mbrus/steward',
                        issue_url='https://github.com/4mbrus/steward/issues',
                        license_type=Gtk.License.GPL_3_0).present(self.props.active_window)


def main():
    return Application().run(sys.argv)
