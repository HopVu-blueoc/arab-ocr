import numpy as np

from app.ocr.crops import (
    Candidate,
    crop_variants,
    order_quad,
    pick_best,
    preprocess_crop,
    warp_quad,
)


def test_order_quad_normalises_point_order():
    # given in scrambled order: br, bl, tl, tr
    scrambled = [(100.0, 60.0), (10.0, 60.0), (10.0, 10.0), (100.0, 10.0)]
    assert order_quad(scrambled) == [
        (10.0, 10.0),
        (100.0, 10.0),
        (100.0, 60.0),
        (10.0, 60.0),
    ]


def test_order_quad_handles_a_slanted_quad():
    # a right-leaning parallelogram, points given clockwise from top-left
    slanted = [(20.0, 10.0), (120.0, 30.0), (110.0, 80.0), (10.0, 60.0)]
    tl, tr, br, bl = order_quad(slanted)
    assert tl == (20.0, 10.0)
    assert tr == (120.0, 30.0)
    assert br == (110.0, 80.0)
    assert bl == (10.0, 60.0)


def test_warp_quad_deskews_a_slanted_box_to_a_rectangle():
    img = np.zeros((200, 300, 3), dtype=np.uint8)
    slanted = [(20.0, 10.0), (120.0, 30.0), (110.0, 80.0), (10.0, 60.0)]

    out = warp_quad(img, slanted)

    # width comes from the long edges (~102px), height from the short ones (~51px)
    assert 95 <= out.shape[1] <= 110
    assert 45 <= out.shape[0] <= 60
    assert out.shape[2] == 3


def test_preprocess_upscales_small_crops_towards_the_target_height():
    crop = np.full((16, 80, 3), 127, dtype=np.uint8)
    out = preprocess_crop(crop, target_height=64, max_upscale=4.0, clahe=False)
    assert out.shape[0] == 64
    assert out.shape[1] == 320


def test_preprocess_respects_the_upscale_cap():
    crop = np.full((8, 40, 3), 127, dtype=np.uint8)
    out = preprocess_crop(crop, target_height=64, max_upscale=2.0, clahe=False)
    assert out.shape[0] == 16  # 8 * 2.0, not 64


def test_preprocess_never_downscales_a_large_crop():
    crop = np.full((120, 400, 3), 127, dtype=np.uint8)
    out = preprocess_crop(crop, target_height=64, max_upscale=4.0, clahe=False)
    assert out.shape[:2] == (120, 400)


def test_clahe_keeps_shape_and_changes_contrast():
    rng = np.random.default_rng(0)
    crop = rng.integers(90, 140, (40, 120, 3), dtype=np.uint8)
    out = preprocess_crop(crop, target_height=40, max_upscale=1.0, clahe=True)
    assert out.shape == crop.shape
    assert out.std() > crop.std()  # contrast stretched


def test_crop_variants_adds_rotations_for_tall_crops():
    tall = np.zeros((100, 20, 3), dtype=np.uint8)
    variants = crop_variants(tall)
    assert len(variants) == 3  # as-is + both 90 degree rotations
    assert variants[1].shape[:2] == (20, 100)
    assert variants[2].shape[:2] == (20, 100)


def test_crop_variants_leaves_wide_crops_alone():
    wide = np.zeros((30, 200, 3), dtype=np.uint8)
    assert len(crop_variants(wide)) == 1


def test_pick_best_takes_the_highest_score():
    best = pick_best(
        [
            Candidate("مرحبا", 0.41, "pipeline"),
            Candidate("مرحبا بالعالم", 0.93, "arabic-rescue"),
            Candidate("Hello", 0.55, "latin-rescue"),
        ]
    )
    assert best.text == "مرحبا بالعالم"
    assert best.source == "arabic-rescue"


def test_pick_best_ignores_blank_results():
    best = pick_best(
        [
            Candidate("السطر", 0.44, "pipeline"),
            Candidate("   ", 0.99, "latin-rescue"),
            Candidate("", 0.98, "arabic-rescue"),
        ]
    )
    assert best.text == "السطر"


def test_pick_best_prefers_pipeline_on_a_tie():
    best = pick_best(
        [
            Candidate("original", 0.80, "pipeline"),
            Candidate("rescued", 0.80, "latin-rescue"),
        ]
    )
    assert best.source == "pipeline"


def test_pick_best_of_all_blanks_is_none():
    assert pick_best([Candidate("", 0.9, "pipeline")]) is None


def test_has_content_rejects_punctuation_only():
    from app.ocr.crops import has_content

    assert has_content("السطر") is True
    assert has_content("510") is True
    assert has_content("Travels") is True
    assert has_content(".") is False
    assert has_content(" - ") is False
    assert has_content("") is False
    assert has_content("···") is False


def test_quad_side_lengths():
    from app.ocr.crops import quad_side_lengths

    w, h = quad_side_lengths([(10.0, 10.0), (110.0, 10.0), (110.0, 40.0), (10.0, 40.0)])
    assert round(w) == 100
    assert round(h) == 30
