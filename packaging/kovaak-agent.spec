# PyInstaller spec for the Windows desktop app. Build with packaging/build.ps1.
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "app" / "__init__.py").read_text()).group(1)
numbers = ", ".join((VERSION.split("-")[0].split(".") + ["0"] * 4)[:4])

version_file = Path(workpath) / "version_info.txt"
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numbers}), prodvers=({numbers})),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Kovaak Agent contributors'),
      StringStruct('FileDescription', 'Kovaak Agent'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'KovaakAgent'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Kovaak Agent contributors. MIT License.'),
      StringStruct('OriginalFilename', 'KovaakAgent.exe'),
      StringStruct('ProductName', 'Kovaak Agent'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "web"), "web")],
    hiddenimports=["pystray._win32"],
    excludes=["tkinter"],
    # pystray is LGPL-3.0: ship it as plain .py files users can replace, not inside the archive.
    module_collection_mode={"pystray": "py"},
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KovaakAgent",
    console=False,
    icon=str(ROOT / "web" / "favicon.ico"),
    version=str(version_file),
)
coll = COLLECT(exe, a.binaries, a.datas, name="KovaakAgent")
