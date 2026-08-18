import io
import time

import pytesseract
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageOps
from pydantic import BaseModel

app = FastAPI(title="Label Extraction Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Labels are often photographed small or at odd angles; upscaling small
# images and boosting contrast measurably improves Tesseract's accuracy
# without the latency cost of a cloud vision call.
MIN_DIMENSION = 1200


class ExtractionResult(BaseModel):
    text: str
    engine: str = "tesseract"
    processing_ms: int


def preprocess(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    image = image.convert("L")

    width, height = image.size
    scale = max(1.0, MIN_DIMENSION / min(width, height))
    if scale > 1.0:
        image = image.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    return ImageOps.autocontrast(image)


def run_extraction(image_bytes: bytes) -> ExtractionResult:
    start = time.monotonic()

    try:
        with Image.open(io.BytesIO(image_bytes)) as raw:
            processed = preprocess(raw)
    except Exception as exc:
        raise HTTPException(400, f"Could not read image: {exc}") from exc

    text = pytesseract.image_to_string(processed)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    return ExtractionResult(text=text, processing_ms=elapsed_ms)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractionResult)
async def extract(file: UploadFile):
    image_bytes = await file.read()
    return run_extraction(image_bytes)
