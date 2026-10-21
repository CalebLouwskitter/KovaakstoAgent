from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from app.desktop import WindowsAutostart, autostart_command


@unittest.skipUnless(sys.platform == "win32", "Windows registry")
class WindowsAutostartTests(unittest.TestCase):
    KEY = r"Software\KovaakAgentTests\Run"

    def setUp(self):
        self.autostart = WindowsAutostart("KovaakAgentTest", self.KEY)

    def tearDown(self):
        import winreg

        for key in (self.KEY, r"Software\KovaakAgentTests"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass

    def test_toggle_writes_and_removes_run_value(self):
        import winreg

        self.assertFalse(self.autostart.autostart_enabled())
        self.autostart.set_autostart(True, r'"C:\Apps\Kovaak Agent.exe" --minimized')
        self.assertTrue(self.autostart.autostart_enabled())
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY) as key:
            self.assertEqual(winreg.QueryValueEx(key, "KovaakAgentTest")[0], r'"C:\Apps\Kovaak Agent.exe" --minimized')
        self.autostart.set_autostart(False)
        self.assertFalse(self.autostart.autostart_enabled())
        self.autostart.set_autostart(False)  # already off is fine

    def test_source_checkout_cannot_enable(self):
        self.assertIsNone(autostart_command())
        self.assertFalse(self.autostart.autostart_available())
        with self.assertRaises(ValueError):
            self.autostart.set_autostart(True)

    def test_installed_app_starts_minimized(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable", r"C:\Apps\Kovaak Agent\KovaakAgent.exe"):
            self.assertEqual(autostart_command(), r'"C:\Apps\Kovaak Agent\KovaakAgent.exe" --minimized')
            self.assertTrue(self.autostart.autostart_available())


if __name__ == "__main__":
    unittest.main()
