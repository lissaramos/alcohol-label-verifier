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
# engine="onnxruntime": Paddle's own inference engine hit a real bug in its
# oneDNN (Intel CPU) backend in production — NotImplementedError:
# ConvertPirAttribute2RuntimeAttribute not support [...DoubleAttribute] — on
# the very first real request. Disabling oneDNN (enable_mkldnn=False) fixed
# the crash but made inference ~10x slower in production (~23s vs a ~2s
# local benchmark), blowing well past the 5-second budget: turns out Paddle's
# un-optimized CPU kernels are genuinely that much slower without it, and a
# 1-CPU cloud instance has none of the 16 cores this was first benchmarked
# on to compensate. Routing inference through ONNX Runtime instead
# sidesteps Paddle's native engine (and its buggy oneDNN integration)
# entirely, while still getting proper CPU-optimized performance — measured
# faster AND lower-memory than either Paddle configuration in local testing.
# Never surfaced in earlier local testing because oneDNN only activates on
# Intel/AMD CPUs, not Apple Silicon.
#
# cpu_threads=2: even with onnxruntime, a real request on Render's 1-CPU
# instance still took ~7s (vs ~0.2-0.3s locally). The default is 10 threads
# — reasonable on a 16-core dev machine, but likely counterproductive on a
# single (v)CPU, where the OS is context-switching between far more threads
# than it has cores to run them on rather than doing useful work. Not
# confirmed locally (nothing to contend with on 16 cores either way), but
# low-risk and worth testing directly on the actual deployment target.
_ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=True,
    engine="onnxruntime",
    cpu_threads=2,
)


# Cropped or small photos (e.g. one half of a front/back label pair) leave
# fine print like the government warning too small for the recognition model
# to read reliably. Upscaling before inference measurably fixed this in
# benchmarking — a cropped label that read "() Consumption" and "s yublity
# o v a cr" at native resolution came back essentially perfect at 1600px.
MIN_DIMENSION = 1600

# The other end of the same problem: a real modern phone photo (e.g.
# 4032x3024, 12MP) sent through this pipeline uncapped measured 6.2GB of
# RSS — enough to OOM almost any reasonably-priced instance. PaddleX has its
# own internal 4000px safety net, but that's still far too large to be
# memory-safe; downscaling to this before inference brought the same photo
# down to a fraction of that with no visible accuracy loss on label text.
MAX_DIMENSION = 1600


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
                width, height = int(width * scale), int(height * scale)

            downscale = min(1.0, MAX_DIMENSION / max(width, height))
            if downscale < 1.0:
                width, height = int(width * downscale), int(height * downscale)

            if (width, height) != processed.size:
                processed = processed.resize((width, height), Image.LANCZOS)
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
