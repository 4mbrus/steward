import os
import tempfile
import threading
import unittest
from unittest import mock

from steward import backends, entries, scanner
from steward.utils import relative_time

ENTRY = '[Desktop Entry]\nType=Application\nName=Chat\nExec=true --chat\n'


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.tmp.name
        self.apps_dir = os.path.join(r, 'share', 'applications')
        os.makedirs(self.apps_dir)
        with open(os.path.join(self.apps_dir, 'chat.desktop'), 'w') as f:
            f.write(ENTRY)
        self.user_auto = os.path.join(r, 'config', 'autostart')
        self.sys_auto = os.path.join(r, 'xdg', 'autostart')
        self.env = mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': os.path.join(r, 'config'),
                                                'XDG_CONFIG_DIRS': os.path.join(r, 'xdg')})
        self.env.start()
        [self.app] = scanner.build_apps(scanner.collect_desktop_files([os.path.join(r, 'share')]), {})

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_enable_copies_entry_and_disable_keeps_the_file(self):
        self.assertFalse(entries.is_autostart(self.app))
        path = entries.set_autostart(self.app, True)
        self.assertTrue(entries.is_autostart(self.app))
        self.assertIn('Exec=true --chat', open(path).read())
        entries.set_autostart(self.app, False)
        self.assertFalse(entries.is_autostart(self.app))
        self.assertTrue(os.path.exists(path))

    def test_apps_own_autostart_options_survive_toggling(self):
        os.makedirs(self.user_auto)
        with open(os.path.join(self.user_auto, 'chat.desktop'), 'w') as f:
            f.write(ENTRY.replace('--chat', '--chat --minimized'))
        self.assertTrue(entries.is_autostart(self.app))
        entries.set_autostart(self.app, False)
        entries.set_autostart(self.app, True)
        text = open(os.path.join(self.user_auto, 'chat.desktop')).read()
        self.assertIn('--minimized', text)
        self.assertTrue(entries.is_autostart(self.app))

    def test_system_autostart_is_overridden_per_user(self):
        os.makedirs(self.sys_auto)
        with open(os.path.join(self.sys_auto, 'chat.desktop'), 'w') as f:
            f.write(ENTRY)
        self.assertTrue(entries.is_autostart(self.app))
        entries.set_autostart(self.app, False)
        self.assertFalse(entries.is_autostart(self.app))
        self.assertIn('Hidden=true', open(os.path.join(self.user_auto, 'chat.desktop')).read())

    def test_disabling_when_never_enabled_writes_nothing(self):
        entries.set_autostart(self.app, False)
        self.assertFalse(os.path.exists(self.user_auto))


class LauncherTest(unittest.TestCase):
    def test_create_unique_launchers(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'XDG_DATA_HOME': d}):
            a = entries.create_launcher('My Script!', '/home/u/run.sh --fast', terminal=True)
            b = entries.create_launcher('My Script!', 'other')
            self.assertEqual(os.path.basename(a), 'my-script.desktop')
            self.assertEqual(os.path.basename(b), 'my-script-2.desktop')
            entry = scanner.parse_desktop_file(a)
            self.assertEqual((entry['Name'], entry['Exec'], entry['Terminal']),
                             ('My Script!', '/home/u/run.sh --fast', 'true'))


class HelpersTest(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(backends.parse_size('1,1 GB'), 1_100_000_000)
        self.assertEqual(backends.parse_size('502.8 kB'), 502_800)
        self.assertEqual(backends.parse_size('1.234,5 MB'), 1_234_500_000)
        self.assertIsNone(backends.parse_size('unknown'))

    def test_relative_time(self):
        now = 1_000_000_000
        self.assertEqual(relative_time(now - 3600, now), 'today')
        self.assertEqual(relative_time(now - 86400 * 1.5, now), 'yesterday')
        self.assertEqual(relative_time(now - 86400 * 15, now), '2 weeks ago')
        self.assertEqual(relative_time(now - 86400 * 800, now), '2 years ago')


class OperationTest(unittest.TestCase):
    def test_streams_lines_and_stops_on_failure(self):
        lines = []
        op = backends.Operation(on_output=lines.append)
        ok, _ = backends.run_commands([['sh', '-c', 'echo a; printf "1\\r2\\n"'], ['false'], ['echo', 'no']], op)
        self.assertFalse(ok)
        self.assertEqual([l for l in lines if not l.startswith('$')], ['a', '1', '2'])

    def test_cancel_stops_unprivileged_command(self):
        op = backends.Operation()
        threading.Timer(0.3, op.cancel).start()
        ok, log = backends.run_commands([['sleep', '5']], op)
        self.assertFalse(ok)
        self.assertTrue(log.endswith('Cancelled.'))

    def test_privileged_commands_are_not_cancellable(self):
        op = backends.Operation()
        op.set_cancellable(False)
        op.cancel()
        self.assertFalse(op.cancelled)


if __name__ == '__main__':
    unittest.main()


class RenameCompatibilityTest(unittest.TestCase):
    def test_settings_move_from_old_app_name(self):
        from steward.utils import migrate_legacy_config
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': d}):
            os.makedirs(os.path.join(d, 'app-commander'))
            with open(os.path.join(d, 'app-commander', 'settings.json'), 'w') as f:
                f.write('{"sort": "size"}')
            migrate_legacy_config()
            self.assertFalse(os.path.exists(os.path.join(d, 'app-commander')))
            self.assertIn('size', open(os.path.join(d, 'steward', 'settings.json')).read())
            migrate_legacy_config()  # second run is a no-op

    def test_overrides_from_old_app_name_are_still_ours(self):
        with tempfile.NamedTemporaryFile('w', suffix='.desktop', delete=False) as f:
            f.write('[Desktop Entry]\nName=X\nX-AppCommander-Override=true\n')
        try:
            self.assertTrue(entries.is_our_override(f.name))
        finally:
            os.unlink(f.name)
