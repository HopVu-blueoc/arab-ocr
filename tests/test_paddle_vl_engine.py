from app.ocr.paddle_vl_engine import parse_spotting_result


def test_parses_polys_and_texts_into_ordered_lines():
    payload = {
        "res": {
            "spotting_res": {
                "rec_polys": [
                    # bottom line, listed first: reading order must fix this
                    [(60, 160), (440, 160), (440, 220), (60, 220)],
                    [(60, 40), (600, 40), (600, 100), (60, 100)],
                ],
                "rec_texts": ["الثاني", "مرحبا بالعالم"],
            }
        }
    }
    lines = parse_spotting_result(payload, width=1000, height=400)
    assert [ln.text for ln in lines] == ["مرحبا بالعالم", "الثاني"]
    assert all(ln.source == "paddleocr-vl" for ln in lines)
    assert all(ln.score == 1.0 for ln in lines)
    assert lines[0].polygon == [(60.0, 40.0), (600.0, 40.0), (600.0, 100.0), (60.0, 100.0)]


def test_accepts_unwrapped_payload_without_res_key():
    payload = {"spotting_res": {"rec_polys": [[(0, 0), (10, 0), (10, 5), (0, 5)]], "rec_texts": ["hi"]}}
    lines = parse_spotting_result(payload, width=10, height=5)
    assert [ln.text for ln in lines] == ["hi"]


def test_drops_punctuation_only_lines():
    payload = {
        "spotting_res": {
            "rec_polys": [
                [(0, 0), (10, 0), (10, 5), (0, 5)],
                [(0, 10), (10, 10), (10, 15), (0, 15)],
            ],
            "rec_texts": ["-", "السطر"],
        }
    }
    lines = parse_spotting_result(payload, width=10, height=15)
    assert [ln.text for ln in lines] == ["السطر"]


def test_missing_spotting_res_yields_no_lines():
    assert parse_spotting_result({"res": {}}, width=100, height=100) == []


def test_empty_texts_and_polys_yields_no_lines():
    payload = {"spotting_res": {"rec_polys": [], "rec_texts": []}}
    assert parse_spotting_result(payload, width=100, height=100) == []
