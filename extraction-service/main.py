import random

from fastapi import FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Extraction Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExtractionResult(BaseModel):
    label: str
    confidence: float


# Placeholder pool used until real extraction logic is wired in.
_PLACEHOLDER_LABELS = ["cat", "dog", "bird", "car", "tree", "person", "unknown"]


def run_extraction(image_bytes: bytes) -> ExtractionResult:
    """Stand-in for the real model/pipeline. Replace this with actual extraction logic."""
    return ExtractionResult(
        label=random.choice(_PLACEHOLDER_LABELS),
        confidence=round(random.uniform(0.6, 0.99), 2),
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractionResult)
async def extract(file: UploadFile):
    image_bytes = await file.read()
    return run_extraction(image_bytes)
