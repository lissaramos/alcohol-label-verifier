import io
import time

import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from paddleocr import PaddleOCR
from PIL import Image, ImageOps
from pydantic import BaseModel

app = FastAPI(title="Label Extraction Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Loaded once at process startup, not per-request — model init/download takes
# ~30s the first time (cached to disk after that), but a request-scoped
# PaddleOCR() would pay a chunk of that on every single call.
#
# Mobile det+rec models chosen over the server variants: ~25% faster
# (benchmarked ~2.1s vs ~2.8s on a real label photo) with no meaningful
# accuracy loss on our labels. use_textline_orientation=True costs almost
# nothing here and noticeably helps labels photographed at an angle.
# Doc orientation/unwarping are for scanned-document photos, not relevant to
# product labels, so they're left off to avoid the extra latency and model
# downloads.
#
# enable_mkldnn=False: Paddle's oneDNN (Intel CPU) backend defaults to on and
# crashed in production with `NotImplementedError:
# ConvertPirAttribute2RuntimeAttribute not support [...DoubleAttribute]` on
# the very first real request — a bug in this Paddle version's oneDNN path,
# not something we can work around other than avoiding it. Never surfaced in
# local testing because that's on Apple Silicon, where oneDNN doesn't apply.
# Falls back to Paddle's standard (non-oneDNN) CPU kernels instead.
_ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=True,
    enable_mkldnn=False,
)


# Cropped or small photos (e.g. one half of a front/back label pair) leave
# fine print like the government warning too small for the recognition model
# to read reliably. Upscaling before inference measurably fixed this in
# benchmarking — a cropped label that read "() Consumption" and "s yublity
# o v a cr" at native resolution came back essentially perfect at 1600px.
MIN_DIMENSION = 1600


class ExtractionResult(BaseModel):
    text: str
    engine: str = "paddleocr-ppocrv5"
    processing_ms: int


def run_extraction(image_bytes: bytes) -> ExtractionResult:
    start = time.monotonic()

    try:
        with Image.open(io.BytesIO(image_bytes)) as raw:
            processed = ImageOps.exif_transpose(raw).convert("RGB")
            width, height = processed.size
            scale = max(1.0, MIN_DIMENSION / min(width, height))
            if scale > 1.0:
                processed = processed.resize(
                    (int(width * scale), int(height * scale)), Image.LANCZOS
                )
            image = np.array(processed)
    except Exception as exc:
        raise HTTPException(400, f"Could not read image: {exc}") from exc

    results = _ocr.predict(image)

    lines = [line for page in results for line in page["rec_texts"]]
    elapsed_ms = int((time.monotonic() - start) * 1000)

    return ExtractionResult(text="\n".join(lines), processing_ms=elapsed_ms)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractionResult)
async def extract(file: UploadFile):
    image_bytes = await file.read()
    # run_extraction is a blocking, CPU-bound call — running it directly here
    # would block the whole event loop and serialize every concurrent
    # request behind it (measured: 4 concurrent requests taking ~4x a single
    # request's latency instead of running alongside each other). Offloading
    # to a thread pool lets FastAPI actually serve requests concurrently.
    return await run_in_threadpool(run_extraction, image_bytes)
