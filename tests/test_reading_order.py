from app.models import Line
from app.ocr.engine import OcrLine
from app.ocr.reading_order import reorder_lines, sort_reading_order


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


def make_line(line_id: int, x0: float, y0: float, w: float = 300, h: float = 60) -> Line:
    return Line(
        id=line_id,
        image_id=1,
        reading_order=0,
        rec_text="x",
        score=0.9,
        polygon=[[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]],
    )


def test_reorder_lines_applies_the_same_band_rtl_ordering():
    # Same layout as test_rtl_within_band_is_rightmost_first, expressed as Line rows.
    lines = [
        make_line(1, 60, 160),  # left-block
        make_line(2, 60, 280),  # bottom
        make_line(3, 440, 165),  # right-block, same band as left-block
        make_line(4, 60, 40),  # top
    ]
    ordered = reorder_lines(lines)
    assert [ln.id for ln in ordered] == [4, 3, 1, 2]


def test_reorder_lines_returns_the_same_objects():
    original = make_line(1, 60, 40)
    (result,) = reorder_lines([original])
    assert result is original


def test_reorder_lines_empty_input():
    assert reorder_lines([]) == []
