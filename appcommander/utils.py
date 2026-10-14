import logging
import os
import threading
import traceback

from gi.repository import Gio, GLib, Gtk, Gdk


def run_async(func, callback=None, *args):
    """Run func(*args) in a thread and deliver (result, error) to callback on the main loop."""
    def worker():
        try:
            result, error = func(*args), None
        except Exception as e:  # surfaced to the UI
            logging.error(traceback.format_exc())
            result, error = None, e
        if callback:
            GLib.idle_add(lambda: callback(result, error) and False)
    threading.Thread(target=worker, daemon=True).start()


def icon_image(icon: str, size: int) -> Gtk.Image:
    image = Gtk.Image(pixel_size=size)
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    if icon and icon.startswith('/') and os.path.isfile(icon):
        image.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(icon)))
    elif icon and theme.has_icon(icon):
        image.set_from_icon_name(icon)
    else:
        image.set_from_icon_name('application-x-executable')
    if size >= 64:
        image.add_css_class('icon-dropshadow')
    return image


def open_containing_folder(window, path: str):
    launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
    launcher.open_containing_folder(window, None, lambda l, r: _finish(l.open_containing_folder_finish, r))


def open_in_text_editor(path: str) -> bool:
    """Open with the default text/plain handler; never with the .desktop handler, which would launch it."""
    app_info = Gio.AppInfo.get_default_for_type('text/plain', False)
    if not app_info:
        return False
    return app_info.launch([Gio.File.new_for_path(path)], None)


def copy_to_clipboard(text: str):
    Gdk.Display.get_default().get_clipboard().set(text)


def trash(path: str) -> None:
    Gio.File.new_for_path(path).trash(None)


def _finish(fn, result):
    try:
        fn(result)
    except GLib.Error as e:
        logging.warning(e.message)
