import os
import tempfile
import unittest
from unittest import mock

from appcommander import entries, scanner

ENTRY = ('[Desktop Entry]\nType=Application\nName=Files\nName[en_US]=Files US\nName[hu]=Fájlok\n'
         'Keywords=folder;\nExec=nautilus\nIcon=org.gnome.Nautilus\n\n[Desktop Action new]\nName=New\n')


class EditingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, 'home')
        self.sys = os.path.join(self.tmp.name, 'usr/share')
        os.makedirs(os.path.join(self.sys, 'applications'))
        with open(os.path.join(self.sys, 'applications', 'files.desktop'), 'w') as f:
            f.write(ENTRY)
        self.env = mock.patch.dict(os.environ, {'XDG_DATA_HOME': self.home, 'LANG': 'en_US.UTF-8',
                                                'LC_ALL': '', 'LC_MESSAGES': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def app(self):
        [app] = scanner.build_apps(scanner.collect_desktop_files([self.home, self.sys]), {})
        return app

    def test_rename_updates_current_locale_and_keeps_other_translations(self):
        path = entries.apply_edits(self.app(), {'Name': 'My Files'})
        self.assertEqual(path, os.path.join(self.home, 'applications', 'files.desktop'))
        text = open(path).read()
        self.assertIn('Name=My Files\n', text)
        self.assertIn('Name[en_US]=My Files\n', text)
        self.assertIn('Name[hu]=Fájlok\n', text)
        self.assertIn('[Desktop Action new]\nName=New', text)  # actions untouched
        self.assertEqual(self.app().name, 'My Files')
        self.assertTrue(entries.is_our_override(path))

    def test_empty_value_removes_key_and_second_edit_reuses_copy(self):
        entries.apply_edits(self.app(), {'Keywords': ''})
        path = entries.apply_edits(self.app(), {'Keywords': 'a;b;'})
        self.assertIn('Keywords=a;b;\n', open(path).read())
        self.assertEqual(open(path).read().count('Keywords='), 1)
        self.assertIn('a b', self.app().search_text())

    def test_store_icon_uses_content_named_copy_and_drops_previous(self):
        app = self.app()
        src = os.path.join(self.tmp.name, 'a.png')
        for content in (b'one', b'two'):
            with open(src, 'wb') as f:
                f.write(content)
            dest = entries.store_icon(app, src)
        icons = os.listdir(os.path.dirname(dest))
        self.assertEqual(icons, [os.path.basename(dest)])
        self.assertEqual(open(dest, 'rb').read(), b'two')


if __name__ == '__main__':
    unittest.main()
