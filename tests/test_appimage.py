import os
import tempfile
import unittest
from unittest import mock

from appcommander import appimage, scanner


class EmbeddedInfoTest(unittest.TestCase):
    def test_github_zsync(self):
        src = appimage.parse_embedded('gh-releases-zsync|FreeCAD|FreeCAD|latest|FreeCAD*x86_64*.AppImage.zsync')
        self.assertEqual(src, {'type': 'github', 'repo': 'FreeCAD/FreeCAD',
                               'pattern': 'FreeCAD*x86_64*.AppImage.zsync', 'prereleases': False, 'tag': None})

    def test_github_prerelease_and_pinned_tag(self):
        self.assertTrue(appimage.parse_embedded('gh-releases-zsync|a|b|latest-pre|x.zsync')['prereleases'])
        self.assertEqual(appimage.parse_embedded('gh-releases-zsync|a|b|v1.2|x.zsync')['tag'], 'v1.2')

    def test_plain_zsync_and_garbage(self):
        self.assertEqual(appimage.parse_embedded('zsync|https://x.org/a.zsync'),
                         {'type': 'zsync', 'url': 'https://x.org/a.zsync'})
        self.assertIsNone(appimage.parse_embedded('bintray-zsync|a|b|c|d'))

    def test_non_appimage_elf_has_no_update_info(self):
        self.assertIsNone(appimage.read_update_info('/usr/bin/true'))
        self.assertFalse(appimage.is_appimage('/usr/bin/true'))


class DesktopEntryTest(unittest.TestCase):
    def test_quote_follows_desktop_spec(self):
        self.assertEqual(appimage.desktop_quote('/home/u/My Apps/a.AppImage'), '"/home/u/My Apps/a.AppImage"')
        self.assertEqual(appimage.desktop_quote('%U'), '%U')
        self.assertEqual(appimage.desktop_quote('a"b$c'), '"a\\"b\\$c"')

    def test_build_entry_rewrites_exec_in_all_groups(self):
        info = appimage.AppImageInfo(path='/x.AppImage', name='My App', version='1.2', desktop_text=(
            '[Desktop Entry]\nType=Application\nName=My App\nExec=env FOO=1 AppRun --no-sandbox %U\n'
            'TryExec=AppRun\nIcon=myapp\n\n[Desktop Action new]\nName=New\nExec=AppRun --new\n'))
        text = info and appimage.build_desktop_entry(info, '/home/u/AppImages/my_app.appimage', '/i/my_app')
        self.assertIn('Exec=/home/u/AppImages/my_app.appimage --no-sandbox %U\n', text)
        self.assertIn('Exec=/home/u/AppImages/my_app.appimage --new', text)
        self.assertIn('TryExec=/home/u/AppImages/my_app.appimage', text)
        self.assertNotIn('TryExec=AppRun', text)
        self.assertIn('Icon=/i/my_app', text)
        self.assertIn('X-AppImage-Version=1.2', text)
        self.assertEqual(info.slug, 'my_app')


class GearLeverImportTest(unittest.TestCase):
    def test_imports_github_and_url_sources(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, 'gearlever.conf')
            with open(conf, 'w') as f:
                f.write('[app.1]\nfile_path = /a.appimage\n[app.1.update_manager]\nmanager = GithubUpdater\n'
                        'repo = o/r\nrepo_filename = R-*.AppImage\nallow_prereleases = True\n'
                        '[app.2]\nfile_path = /b.appimage\n[app.2.update_manager]\n'
                        'manager = StaticFileUpdater\nurl = https://x/b.AppImage\n'
                        '[app.3]\nfile_path = /c.appimage\n[app.3.update_manager]\n')
            with mock.patch.object(appimage, 'GEARLEVER_CONF', conf), \
                    mock.patch.object(appimage, 'CONFIG_PATH', os.path.join(d, 'cfg', 'u.json')):
                self.assertEqual(appimage.get_custom_source('/a.appimage'),
                                 {'type': 'github', 'repo': 'o/r', 'pattern': 'R-*.AppImage', 'prereleases': True})
                self.assertEqual(appimage.get_custom_source('/b.appimage'),
                                 {'type': 'url', 'url': 'https://x/b.AppImage'})
                self.assertIsNone(appimage.get_custom_source('/c.appimage'))


class BrokenEntryTest(unittest.TestCase):
    def entry(self, **kw):
        return scanner.DesktopFile('/x.desktop', 'x.desktop', '/', kw)

    def test_missing_try_exec_or_program(self):
        self.assertEqual(scanner.missing_program(self.entry(TryExec='/nope/bin', Exec='true')), '/nope/bin')
        self.assertEqual(scanner.missing_program(self.entry(Exec='/app/bin/Cemu %u')), '/app/bin/Cemu')
        self.assertEqual(scanner.missing_program(self.entry(Exec='env A=1 not-a-real-program-xyz')),
                         'not-a-real-program-xyz')

    def test_present_programs(self):
        self.assertIsNone(scanner.missing_program(self.entry(Exec='true --flag %F')))
        self.assertIsNone(scanner.missing_program(self.entry(Exec='env A=1 /usr/bin/true')))


if __name__ == '__main__':
    unittest.main()
