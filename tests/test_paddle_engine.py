from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "arabic_sample.png"


@pytest.mark.slow
def test_paddle_engine_reads_the_fixture():
    from app.ocr.paddle_engine import PaddleOcrEngine

    result = PaddleOcrEngine().run(FIXTURE)

    assert result.width == 1000
    assert result.height == 400
    assert len(result.lines) == 4
    assert any("؀" <= ch <= "ۿ" for ln in result.lines for ch in ln.text)

    # Paddle detects band 2 left-to-right (الثاني then السطر); reading order must
    # flip it to rightmost-first. This is the whole point of sort_reading_order.
    assert [ln.text for ln in result.lines] == [
        "مرحبا بالعالم",
        "السطر",
        "الثاني",
        "اختبار التعرف الضوئي",
    ]

    # Bands run top to bottom.
    tops = [min(p[1] for p in ln.polygon) for ln in result.lines]
    assert tops[0] < tops[1] and tops[2] < tops[3]
