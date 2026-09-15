from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    redis_url: str = "redis://localhost:6379/0"
    job_backend: str = "inline"  # inline | celery
    ocr_device: str = "cpu"

    # local = a directory under DATA_DIR (dev default; tests and dev.sh must
    # not need an object store running). s3 = any S3-compatible store; on-prem
    # this is RustFS. See docker-compose.yml.
    storage_backend: str = "local"  # local | s3
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "ocr-images"
    s3_region: str = "us-east-1"
    # Never commit real values - this repo is public. Set them in .env.
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # Per-file upload cap. Enforced while streaming, so an oversize file never
    # gets fully written. Scene photos from a phone are 3-8MB.
    max_upload_mb: int = 25

    # Cap on one multipart request's total size. Separate from the per-file cap
    # because the proxy rejects on the whole body: 20 files of 3MB each are
    # individually fine and together a 413. The frontend reads this from
    # /api/limits and packs requests to fit, and docker/nginx.conf's
    # client_max_body_size must stay at or above it (tests/test_limits.py).
    max_request_mb: int = 100

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * (1 << 20)

    @property
    def max_request_bytes(self) -> int:
        return self.max_request_mb * (1 << 20)

    # Which OcrEngine to build.
    #
    # "paddle" (default, the only supported value for production) = the
    # detector+CRNN pipeline configured below: arabic_PP-OCRv5_mobile_rec for
    # recognition, PP-OCRv5_server_det for detection.
    #
    # "paddle_vl" = PaddleOCR-VL, a ~1B-param vision-language model.
    # EXPERIMENTAL, not for on-prem deployment: it hangs on this Mac's CPU
    # (see README) and at ~1B params it is too heavy for batch throughput on a
    # large corpus. Kept wired up because it sits behind the OcrEngine
    # protocol and costs nothing to leave in place.
    ocr_engine: str = "paddle"
    ocr_vl_model_name: str = "PaddleOCR-VL-1.6-0.9B"
    ocr_vl_backend: str = "native"

    ocr_rec_model: str = "arabic_PP-OCRv5_mobile_rec"
    # Server detector: ~10x slower on CPU than mobile (4.8s vs 0.45s per
    # image) and on image 16 it found fewer boxes, but accuracy is the
    # priority here and mobile_det is the weakest tier available. Switch to
    # PP-OCRv5_mobile_det via OCR_DET_MODEL if throughput starts to matter.
    #
    # NOTE: there is no Arabic *server* recogniser. PaddleOCR ships Arabic
    # only as arabic_PP-OCRv5_mobile_rec (7.6MB) / _PP-OCRv3_mobile_rec, and
    # PP-OCRv6's 50 languages do not include Arabic. The recogniser, not the
    # detector, is the accuracy ceiling - see README for the upgrade path.
    ocr_det_model: str = "PP-OCRv5_server_det"
    reviewer_name: str = "reviewer"

    # --- detection knobs -----------------------------------------------
    # All None: use PaddleOCR's own defaults. These are per-corpus tuning
    # knobs, not settings with a universally right value - measured on
    # tests/data/pack1, raising limit_side_len to 1280/1920 split the sign
    # "كوكب الجمال" in two, while on tests/fixtures (1000px image, 48px text)
    # *lowering* it to 736 split "مرحبا بالعالم" instead. What matters is text
    # height in pixels, so tune with scripts/bench_ocr.py on your own images.
    ocr_det_limit_side_len: int | None = None
    ocr_det_thresh: float | None = None
    ocr_det_box_thresh: float | None = None
    ocr_det_unclip_ratio: float | None = None
    # Keep the pipeline's own filter open so weak lines survive long enough to
    # be re-read by the second pass; the real filter is applied afterwards.
    ocr_pipeline_rec_score_thresh: float = 0.0
    # Final gate. Deliberately low: this is a review tool, and a weak-but-real
    # line the reviewer can fix beats a line silently dropped.
    ocr_rec_score_thresh: float = 0.3

    # Per-line orientation classification. Measured regression on
    # tests/data/pack1/image (15).png: it misclassified one crop's rotation
    # and flipped a correctly-read "510" (1.000) into "OLS" (0.993) - a
    # confident, silent wrong answer. Off until this can be scoped to only
    # the crops that actually need it (tall/narrow ones), matching the
    # crop_variants() heuristic already used in the second pass.
    ocr_use_textline_orientation: bool = False
    # Whole-image rotation detection. Off: phone photos of shopfronts are
    # already upright and it adds a model pass per image.
    ocr_use_doc_orientation_classify: bool = False
    # Perspective dewarping. Helps photographed documents, can distort scene
    # photos, so it stays off by default and is worth A/B-ing per corpus.
    ocr_use_doc_unwarping: bool = False

    # --- second recognition pass ---------------------------------------
    # Re-read each detected line from a deskewed, upscaled crop using both the
    # Arabic and the Latin recogniser, then keep whichever scores highest.
    ocr_second_pass: bool = True
    # Only lines at or below this score are re-read. Re-reading a line the
    # pipeline already scored 0.99 cannot improve it and dominated the runtime
    # (43s/image), so the rescue is aimed at the weak reads that need it.
    ocr_second_pass_max_score: float = 0.85
    ocr_latin_rec_model: str = "latin_PP-OCRv5_mobile_rec"
    # A rescue must beat the pipeline by this margin to replace it. Scores
    # are not calibrated between the Arabic and Latin models, so a confidently
    # wrong Latin read ("510" -> "OLS" at 0.98) would otherwise win.
    ocr_rescue_min_gain: float = 0.15
    ocr_crop_target_height: int = 64
    ocr_crop_max_upscale: float = 4.0
    ocr_crop_clahe: bool = True

    # --- noise filters --------------------------------------------------
    # Minimum side of a detected quad, in source pixels. Set to 0 (off) after
    # measuring: at 12 it deleted correctly-read lines - "TRUCKS ONLY" (0.990)
    # and "SILKS" (0.976) - because sign text photographed at distance is
    # genuinely only a few pixels tall yet still reads fine. Small is not the
    # same as spurious. Results with no letter or digit are still dropped.
    ocr_min_box_side: int = 0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
