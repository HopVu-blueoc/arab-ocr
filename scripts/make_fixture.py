"""Generate tests/fixtures/arabic_sample.png. Run once; commit the PNG."""

from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/System/Library/Fonts/GeezaPro.ttc"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "arabic_sample.png"

# Three lines, top to bottom. Line 2 is deliberately two separate blocks so the
# reading-order test has a right-to-left pair to sort.
LINES = [
    ["مرحبا بالعالم"],
    ["الثاني", "السطر"],  # drawn left-to-right on the canvas
    ["اختبار التعرف الضوئي"],
]


def render(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))


def main() -> None:
    width, height = 1000, 400
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 48, layout_engine=ImageFont.Layout.BASIC)

    y = 40
    for blocks in LINES:
        x = 60
        for block in blocks:
            draw.text((x, y), render(block), font=font, fill="black")
            x += 380
        y += 120

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"wrote {OUT} ({width}x{height})")


if __name__ == "__main__":
    main()
