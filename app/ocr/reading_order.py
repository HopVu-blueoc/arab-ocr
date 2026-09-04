from collections.abc import Sequence

from app.ocr.engine import OcrLine, bbox


def sort_reading_order(
    lines: Sequence[OcrLine],
    *,
    rtl: bool = True,
    overlap_ratio: float = 0.5,
) -> list[OcrLine]:
    """Order detected lines the way a human reads them.

    Lines are grouped into horizontal bands (vertical overlap >= overlap_ratio of
    the shorter line's height); bands run top to bottom; inside a band lines run
    right to left when rtl is True. Text is never modified.
    """
    bands: list[dict] = []
    for ln in sorted(lines, key=lambda line: bbox(line.polygon)[1]):
        _, y0, _, y1 = bbox(ln.polygon)
        for band in bands:
            overlap = min(y1, band["y1"]) - max(y0, band["y0"])
            shorter = min(y1 - y0, band["y1"] - band["y0"])
            if shorter > 0 and overlap / shorter >= overlap_ratio:
                band["lines"].append(ln)
                band["y0"] = min(band["y0"], y0)
                band["y1"] = max(band["y1"], y1)
                break
        else:
            bands.append({"y0": y0, "y1": y1, "lines": [ln]})

    ordered: list[OcrLine] = []
    for band in bands:
        band["lines"].sort(key=lambda line: bbox(line.polygon)[0], reverse=rtl)
        ordered.extend(band["lines"])
    return ordered
