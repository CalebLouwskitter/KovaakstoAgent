# Third-party notices

## daisyUI 5.7.22

The bundled `web/vendor/daisyui.css` identifies itself as daisyUI 5.7.22.
The following notice is reproduced from the [upstream v5.7.22 license](https://github.com/saadeghi/daisyui/blob/v5.7.22/LICENSE).
It applies to daisyUI, not automatically to this project's own code.

MIT License

Copyright (c) 2020 Pouya Saadeghi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Windows desktop build

The Windows installer and `dist\KovaakAgent` folder also bundle the following components, each under its own license. They are not part of the source-only `python -m app.server` workflow, which uses only the Python standard library.

| Component | License | Source |
| --- | --- | --- |
| CPython runtime | PSF License 2.0 | https://www.python.org/ |
| pywebview | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| bottle (pywebview dependency) | MIT | https://github.com/bottlepy/bottle |
| proxy_tools (pywebview dependency) | MIT | https://pypi.org/project/proxy_tools/ |
| typing_extensions | PSF License 2.0 | https://github.com/python/typing_extensions |
| pythonnet and clr_loader | MIT | https://github.com/pythonnet/pythonnet |
| Microsoft Edge WebView2 loader and .NET interop assemblies (via pywebview) | Microsoft license, redistributable | https://developer.microsoft.com/microsoft-edge/webview2/ |
| pystray | LGPL-3.0 | https://github.com/moses-palmer/pystray |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow |
| PyInstaller bootloader | GPL-2.0-or-later with the PyInstaller bootloader exception | https://github.com/pyinstaller/pyinstaller |

pystray is shipped as unmodified `.py` files in the install folder's `_internal\pystray` directory, so it can be replaced with another compatible version. Its source is available at the link above. The application's own code remains under the MIT License.
