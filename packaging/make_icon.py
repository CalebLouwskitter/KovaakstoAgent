"""Draw the app icon (orange tile with a crosshair) into web/favicon.ico.

The committed icon is used by the window, tray, installer and browser tab.
Rerun after changing the design: python packaging/make_icon.py (needs Pillow).
"""

from pathlib import Path

from PIL import Image, ImageDraw

ORANGE = (245, 138, 36, 255)
INK = (41, 22, 4, 255)
SIZE = 1024
TARGET = Path(__file__).resolve().parents[1] / "web" / "favicon.ico"


def draw() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle((40, 40, SIZE - 40, SIZE - 40), radius=220, fill=ORANGE)
    center, ring, width = SIZE // 2, 270, 76
    pen.ellipse((center - ring, center - ring, center + ring, center + ring), outline=INK, width=width)
    gap, reach, bar = 120, 390, 70
    for start, end in ((center - reach, center - gap), (center + gap, center + reach)):
        pen.rounded_rectangle((center - bar // 2, start, center + bar // 2, end), radius=bar // 2, fill=INK)
        pen.rounded_rectangle((start, center - bar // 2, end, center + bar // 2), radius=bar // 2, fill=INK)
    dot = 58
    pen.ellipse((center - dot, center - dot, center + dot, center + dot), fill=INK)
    return image


if __name__ == "__main__":
    draw().save(TARGET, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Wrote {TARGET}")
