from app.ocr.crops import Candidate
from app.ocr.fake_engine import FakeOcrEngine


def test_recognize_quad_returns_configured_result_and_records_the_call():
    result = Candidate("يدوي", 0.93, "manual")
    engine = FakeOcrEngine(recognize_quad_result=result)

    got = engine.recognize_quad(image=None, polygon=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])

    assert got is result
    assert engine.recognize_quad_calls == [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]]


def test_recognize_quad_can_be_configured_to_find_nothing():
    engine = FakeOcrEngine(recognize_quad_result=None)
    assert engine.recognize_quad(image=None, polygon=[(0.0, 0.0), (1.0, 1.0)]) is None
