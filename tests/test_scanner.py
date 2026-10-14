import os
import tempfile
import unittest

from steward import entries, scanner


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


ENTRY = '[Desktop Entry]\nType=Application\nName={name}\nExec={exec}\n{extra}\n[Desktop Action new]\nName=New\n'


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.home = os.path.join(self.root, 'home')
        self.sys = os.path.join(self.root, 'usr/share')
        self.fp = os.path.join(self.root, 'var/lib/flatpak/exports/share')
        os.environ['XDG_DATA_HOME'] = self.home

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, base, name, desktop_id, exec_='app', extra=''):
        path = os.path.join(base, 'applications', desktop_id)
        write(path, ENTRY.format(name=name, exec=exec_, extra=extra))
        return path

    def scan(self, owners=None):
        return scanner.build_apps(scanner.collect_desktop_files([self.home, self.sys, self.fp]), owners or {})

    def test_user_entry_overrides_system_entry_and_keeps_package(self):
        sys_path = self.entry(self.sys, 'Files', 'org.gnome.Nautilus.desktop', 'nautilus')
        self.entry(self.home, 'Files (mine)', 'org.gnome.Nautilus.desktop', 'nautilus', 'NoDisplay=true')
        [app] = self.scan({sys_path: 'nautilus'})
        self.assertEqual(app.name, 'Files (mine)')
        self.assertTrue(app.hidden)
        self.assertEqual((app.backend, app.package), (scanner.APT, 'nautilus'))
        self.assertIsNotNone(app.user_override)

    def test_detects_flatpak_with_installation(self):
        self.entry(self.fp, 'Blender', 'org.blender.Blender.desktop', 'flatpak run org.blender.Blender',
                   'X-Flatpak=org.blender.Blender')
        [app] = self.scan()
        self.assertEqual((app.backend, app.package, app.installation),
                         (scanner.FLATPAK, 'org.blender.Blender', 'system'))

    def test_detects_appimage_from_exec_with_env_prefix(self):
        self.entry(self.home, 'FreeCAD', 'freecad.desktop',
                   'env DESKTOPINTEGRATION=1 /home/u/AppImages/freecad.AppImage --single-instance %F')
        [app] = self.scan()
        self.assertEqual(app.backend, scanner.APPIMAGE)
        self.assertEqual(app.appimage_path, '/home/u/AppImages/freecad.AppImage')

    def test_unowned_entry_is_local_and_subdirs_form_desktop_id(self):
        self.entry(self.home, 'Web App', 'kde4/webapp.desktop')
        [app] = self.scan()
        self.assertEqual((app.backend, app.desktop_id), (scanner.LOCAL, 'kde4-webapp.desktop'))

    def test_non_application_entries_are_skipped(self):
        path = os.path.join(self.home, 'applications', 'link.desktop')
        write(path, '[Desktop Entry]\nType=Link\nName=Docs\nURL=https://example.com\n')
        self.assertEqual(self.scan(), [])

    def test_only_desktop_entry_group_is_read(self):
        self.entry(self.home, 'Editor', 'editor.desktop')
        [app] = self.scan()
        self.assertEqual(app.name, 'Editor')  # not "New" from the action group


class EntriesTest(unittest.TestCase):
    def test_set_key_replaces_inside_main_group_only(self):
        text = '[Desktop Entry]\nName=A\nNoDisplay=false\n\n[Desktop Action x]\nNoDisplay=false\n'
        out = entries.set_key(text, 'NoDisplay', 'true')
        self.assertIn('[Desktop Entry]\nName=A\nNoDisplay=true\n', out)
        self.assertTrue(out.rstrip().endswith('[Desktop Action x]\nNoDisplay=false'))

    def test_set_key_inserts_before_next_group(self):
        text = '[Desktop Entry]\nName=A\n\n[Desktop Action x]\nName=X\n'
        out = entries.set_key(text, 'NoDisplay', 'true')
        self.assertIn('Name=A\nNoDisplay=true\n\n[Desktop Action x]', out)

    def test_set_hidden_writes_marked_user_copy(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ['XDG_DATA_HOME'] = os.path.join(root, 'home')
            sys_dir = os.path.join(root, 'usr/share')
            write(os.path.join(sys_dir, 'applications/a.desktop'), ENTRY.format(name='A', exec='a', extra=''))
            [app] = scanner.build_apps(scanner.collect_desktop_files([sys_dir]), {})
            path = entries.set_hidden(app, True)
            self.assertEqual(path, os.path.join(root, 'home/applications/a.desktop'))
            self.assertTrue(entries.is_our_override(path))
            self.assertIn('NoDisplay=true', open(path).read())
            self.assertIn('NoDisplay=true', open(entries.set_hidden(app, True)).read())


if __name__ == '__main__':
    unittest.main()
