from app.ocr.engine import OcrLine
from app.ocr.reading_order import sort_reading_order


def line(text: str, x0: float, y0: float, w: float = 300, h: float = 60) -> OcrLine:
    return OcrLine(
        text=text,
        score=0.9,
        polygon=[(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)],
    )


def test_rtl_within_band_is_rightmost_first():
    # Detection order is deliberately scrambled.
    lines = [
        line("left-block", 60, 160),
        line("bottom", 60, 280),
        line("right-block", 440, 165),  # same band as left-block (5px offset)
        line("top", 60, 40),
    ]
    ordered = [ln.text for ln in sort_reading_order(lines)]
    assert ordered == ["top", "right-block", "left-block", "bottom"]


def test_ltr_flag_flips_within_band_order():
    lines = [line("a", 60, 40), line("b", 440, 40)]
    assert [ln.text for ln in sort_reading_order(lines, rtl=False)] == ["a", "b"]


def test_does_not_mutate_or_rewrite_text():
    original = line("مرحبا بالعالم", 60, 40)
    (result,) = sort_reading_order([original])
    assert result.text == "مرحبا بالعالم"
    assert result is original


def test_empty_input():
    assert sort_reading_order([]) == []
