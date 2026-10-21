"""Locate KovaaK's stats folders across Steam libraries.

Steam's install folder comes from the registry or common locations. Each
install lists its extra libraries in steamapps/libraryfolders.vdf. KovaaK's
writes per-run CSVs to <library>/steamapps/common/FPSAimTrainer/FPSAimTrainer/stats.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterable


STATS_SUBPATH = Path("steamapps", "common", "FPSAimTrainer", "FPSAimTrainer", "stats")
_VDF_PATH = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"', re.IGNORECASE)


def library_paths_from_vdf(text: str) -> list[Path]:
    """Extract library folders from the text of a libraryfolders.vdf file."""
    return [Path(re.sub(r"\\(.)", r"\1", match)) for match in _VDF_PATH.findall(text)]


def _registry_steam_paths() -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg

    found = []
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                found.append(Path(winreg.QueryValueEx(handle, value)[0]))
        except OSError:
            continue
    return found


def _fixed_drives() -> list[str]:
    """Local fixed drive roots such as 'C:\\'; skips network and removable drives."""
    if sys.platform != "win32":
        return []
    import ctypes

    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    drives = []
    for index in range(26):
        if mask & (1 << index):
            root = f"{chr(65 + index)}:\\"
            if kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
                drives.append(root)
    return drives


def default_steam_roots() -> list[Path]:
    """Likely Steam install or library folders on this computer."""
    roots = _registry_steam_paths()
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]) / "Steam")
    for drive in _fixed_drives():
        for name in ("SteamLibrary", "Steam", r"Games\Steam", r"Games\SteamLibrary", r"Program Files (x86)\Steam"):
            roots.append(Path(drive) / name)
    home = Path.home()
    roots += [home / ".steam" / "steam", home / ".local" / "share" / "Steam"]
    return roots


def find_stats_folders(steam_roots: Iterable[Path] | None = None) -> list[Path]:
    """Return existing KovaaK's stats folders, those already holding CSVs first."""
    libraries: list[Path] = []
    for root in default_steam_roots() if steam_roots is None else steam_roots:
        libraries.append(Path(root))
        try:
            text = (Path(root) / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        libraries.extend(library_paths_from_vdf(text))

    seen: set[str] = set()
    folders: list[Path] = []
    for library in libraries:
        candidate = library / STATS_SUBPATH
        try:
            if not candidate.is_dir():
                continue
            key = os.path.normcase(str(candidate.resolve()))
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            folders.append(candidate)

    def has_csv(folder: Path) -> bool:
        try:
            return any(folder.glob("*.csv"))
        except OSError:
            return False

    return sorted(folders, key=lambda folder: not has_csv(folder))
