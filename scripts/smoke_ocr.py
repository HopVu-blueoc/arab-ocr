"""Real-PaddleOCR smoke test. Prints resolved model names and recognised text."""

import sys
from pathlib import Path

from paddleocr import PaddleOCR

IMAGE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "arabic_sample.png"


def main() -> int:
    ocr = PaddleOCR(
        lang="ar",
        ocr_version="PP-OCRv5",
        text_recognition_model_name="arabic_PP-OCRv5_mobile_rec",
        text_detection_model_name="PP-OCRv5_mobile_det",
        device="cpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    results = ocr.predict(str(IMAGE))
    payload = results[0].json
    data = payload.get("res", payload)  # 3.x wraps the dict under "res"

    print("rec_texts:")
    for text, score in zip(data["rec_texts"], data["rec_scores"], strict=True):
        print(f"  {score:.3f}  {text}")
    print(f"boxes: {len(data['rec_polys'])}")

    if not data["rec_texts"]:
        print("FAIL: no text recognised", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
