"""Windows desktop shell: a native window and tray icon around the local server.

Runs the same local HTTP server as `python -m app.server`, shown in a WebView2
window through pywebview instead of a browser tab. Closing the window keeps the
app (and the stats watcher) running in the tray; choose Quit from the tray menu
to exit. A second launch brings the existing window forward.

Requires the desktop extras (requirements-desktop.txt) and Windows with the
Microsoft Edge WebView2 runtime.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import threading
import traceback
from pathlib import Path

from . import __version__
from .paths import APP_NAME, resource_root, user_data_dir
from .server import ApiHandler, create_server


DESKTOP_PORT = 8766
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "KovaakAgent"
MUTEX_NAME = "Local\\KovaakAgentDesktop"
ICON_PATH = resource_root() / "web" / "favicon.ico"
_instance_mutex = None


def autostart_command() -> str | None:
    """Command line the Run key should hold, or None when not running as the installed app."""
    if not getattr(sys, "frozen", False):
        return None
    return f'"{sys.executable}" --minimized'


class WindowsAutostart:
    """Start-with-Windows toggle stored in HKCU\\...\\Run (no admin rights needed)."""

    def __init__(self, value_name: str = RUN_VALUE, key_path: str = RUN_KEY):
        self.value_name = value_name
        self.key_path = key_path

    def autostart_available(self) -> bool:
        return sys.platform == "win32" and autostart_command() is not None

    def autostart_enabled(self) -> bool:
        if sys.platform != "win32":
            return False
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path) as key:
                return bool(winreg.QueryValueEx(key, self.value_name)[0])
        except OSError:
            return False

    def set_autostart(self, enabled: bool, command: str | None = None) -> None:
        import winreg

        command = command or autostart_command()
        if enabled and not command:
            raise ValueError("Start with Windows is available in the installed app.")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.key_path) as key:
            if enabled:
                winreg.SetValueEx(key, self.value_name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, self.value_name)
                except FileNotFoundError:
                    pass


def _already_running() -> bool:
    """Hold a per-session mutex; if another instance owns it, raise that window instead."""
    global _instance_mutex
    kernel32 = ctypes.windll.kernel32
    _instance_mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return False
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, APP_NAME)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return True


def _show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)


def _redirect_output(data_dir: Path) -> None:
    """The installed app has no console; keep request logs and errors in a file instead."""
    if not getattr(sys, "frozen", False) and sys.stdout is not None and sys.stderr is not None:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = open(data_dir / "kovaak-agent.log", "w", encoding="utf-8", buffering=1)


def _bind_server(data_dir: Path):
    try:
        return create_server("127.0.0.1", DESKTOP_PORT, data_dir)
    except OSError:
        # Another program holds the usual port; any free port works for the window.
        return create_server("127.0.0.1", 0, data_dir)


def run(minimized: bool = False) -> None:
    import pystray
    import webview
    from PIL import Image

    data_dir = user_data_dir()
    _redirect_output(data_dir)
    server, service = _bind_server(data_dir)
    autostart = WindowsAutostart()
    ApiHandler.desktop = autostart
    service.start()
    threading.Thread(target=server.serve_forever, name="local-server", daemon=True).start()
    print(f"{APP_NAME} {__version__} at http://127.0.0.1:{server.server_port}, data in {data_dir}")

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        APP_NAME, f"http://127.0.0.1:{server.server_port}/",
        width=1280, height=860, min_size=(900, 640), hidden=minimized,
        background_color="#f2f2f2",
    )
    quitting = threading.Event()

    def show_window(*_args) -> None:
        window.show()
        window.restore()

    def quit_app(*_args) -> None:
        quitting.set()
        tray.stop()
        window.destroy()

    def toggle_autostart(*_args) -> None:
        try:
            autostart.set_autostart(not autostart.autostart_enabled())
        except (OSError, ValueError) as exc:
            tray.notify(str(exc), APP_NAME)

    def on_closing() -> bool:
        if quitting.is_set():
            return True
        threading.Thread(target=window.hide, daemon=True).start()
        if service.database.get_setting("tray_hint_shown") != "true":
            service.database.set_setting("tray_hint_shown", "true")
            tray.notify("Still watching for new runs. Right-click the tray icon to quit.", APP_NAME)
        return False

    window.events.closing += on_closing
    tray = pystray.Icon(
        "kovaak-agent", Image.open(ICON_PATH), APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem("Open Kovaak Agent", show_window, default=True),
            pystray.MenuItem("Start with Windows", toggle_autostart,
                             checked=lambda _item: autostart.autostart_enabled(),
                             visible=autostart.autostart_available()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app),
        ),
    )
    threading.Thread(target=tray.run, name="tray", daemon=True).start()
    try:
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=str(data_dir / "webview"))
    finally:
        tray.stop()
        service.stop()
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Run {APP_NAME} in its own window.")
    parser.add_argument("--minimized", action="store_true", help="Start in the tray without opening the window.")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("The desktop app supports Windows. Use `python -m app.server` elsewhere.")
    if _already_running():
        return
    try:
        run(minimized=args.minimized)
    except Exception:
        details = traceback.format_exc()
        print(details, file=sys.stderr)
        _show_error(f"{APP_NAME} could not start.\n\n{details.strip().splitlines()[-1]}\n\n"
                    "If the window stays blank, install the Microsoft Edge WebView2 Runtime.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
