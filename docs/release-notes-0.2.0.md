# Kovaak Agent 0.2.0 Preview

Kovaak Agent now installs and runs as a Windows desktop app. It's still a **preview**: live model coaching remains unverified.

## New

- **Windows installer.** `KovaakAgent-Setup-0.2.0.exe` installs per user (no admin prompt) into `%LOCALAPPDATA%\Programs\Kovaak Agent` and adds a Start menu shortcut. A desktop shortcut and start-with-Windows are optional during setup. Python isn't needed.
- **Own window.** The app opens in a native window (Microsoft Edge WebView2) instead of a browser tab. A second launch brings the open window to the front.
- **Tray.** Closing the window keeps the app in the notification area, so new runs are still captured. Use the tray icon to reopen, toggle **Start with Windows**, or **Quit**.
- **Start with Windows.** Available in setup, in the tray menu and in **Connections**. It starts in the tray without opening the window.
- **Finds your stats folder.** On first run the app looks through your Steam libraries (registry, `libraryfolders.vdf` and common folders on every fixed drive) for `FPSAimTrainer\stats` and starts watching it. **Find it in my Steam libraries** in Connections repeats the search. A path you set or clear yourself is never replaced.
- **Data in your profile.** The database now lives in `%APPDATA%\Kovaak Agent\kovaaks-agent.db` instead of the project's `data/` folder. Connections shows the folder. Override it with `--data-dir` or `KOVAAK_AGENT_DATA_DIR`.
- **App icon** for the window, tray, installer and browser tab.

## Fixed

- Refused requests (wrong host, cross-origin, or not JSON) now read and discard a small request body before replying. Windows sometimes reset the connection instead of delivering the refusal, which made the request-security tests fail intermittently.

## Upgrading from 0.1.0

If you ran 0.1.0 from source, start 0.2.0 once from that same folder with `python -m app.server`. It copies `data/kovaaks-agent.db` (including changes still in its write-ahead log) to `%APPDATA%\Kovaak Agent` and leaves the original file as a safety copy. The installed app then uses the same data.

Prefer the installer only? Close every Kovaak Agent instance, then copy `data\kovaaks-agent.db` from the old folder into `%APPDATA%\Kovaak Agent\` before the first launch.

## Still works from source

`python -m app.server` still serves the app in your browser with only the standard library. It uses the same data folder as the desktop app; don't run both against one database at once. The desktop app uses port 8766 (or any free port), and the browser server still defaults to 8765.

## Build

`packaging/build.ps1` creates a virtual environment, installs `requirements-desktop.txt` (pywebview, pystray, Pillow, PyInstaller), runs the tests, bundles the app with PyInstaller and compiles the installer with Inno Setup 6. Pushing a `v*` tag runs the same build on GitHub Actions and attaches the installer to a draft release.

## Validation and limitations

**57 automated tests pass**, including new tests for data-folder migration, Steam library detection, first-run auto-detection, start-with-Windows registry handling and the new local API endpoints. The installer was built and installed on Windows 11, and the installed app launched and auto-detected a real KovaaK's stats folder.

- Live OpenAI, OpenRouter and Gemini coaching is still untested (no API credentials). Provider tests use mocks.
- The installer isn't code-signed, so Windows SmartScreen may warn on first run.
- Only Windows 11 x64 was tested. The desktop app needs the WebView2 runtime, which ships with Windows 11 and current Windows 10. Other operating systems can still use `python -m app.server`.
- Uninstalling keeps your data in `%APPDATA%\Kovaak Agent`. Delete that folder to remove it.
