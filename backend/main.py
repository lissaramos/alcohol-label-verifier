import os
import uuid
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

import models
import schemas
from database import Base, SessionLocal, engine, get_db

Base.metadata.create_all(bind=engine)

STORAGE_DIR = Path(__file__).parent / "storage"
STORAGE_DIR.mkdir(exist_ok=True)

EXTRACTION_SERVICE_URL = os.environ.get("EXTRACTION_SERVICE_URL", "http://localhost:9000")

app = FastAPI(title="Labeler API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/images", response_model=schemas.ImageOut)
async def upload_image(file: UploadFile, db: Session = Depends(get_db)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    ext = Path(file.filename or "").suffix
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = STORAGE_DIR / stored_name

    contents = await file.read()
    dest.write_bytes(contents)

    image = models.Image(
        filename=stored_name,
        original_name=file.filename or stored_name,
        content_type=file.content_type,
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return image


@app.get("/images", response_model=list[schemas.ImageOut])
def list_images(db: Session = Depends(get_db)):
    return db.query(models.Image).order_by(models.Image.created_at.desc()).all()


@app.get("/images/{image_id}", response_model=schemas.ImageDetailOut)
def get_image(image_id: int, db: Session = Depends(get_db)):
    image = db.get(models.Image, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    return image


@app.get("/images/{image_id}/file")
def get_image_file(image_id: int, db: Session = Depends(get_db)):
    image = db.get(models.Image, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    path = STORAGE_DIR / image.filename
    if not path.exists():
        raise HTTPException(404, "Image file missing")
    return FileResponse(path, media_type=image.content_type)


@app.delete("/images/{image_id}", status_code=204)
def delete_image(image_id: int, db: Session = Depends(get_db)):
    image = db.get(models.Image, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    path = STORAGE_DIR / image.filename
    if path.exists():
        path.unlink()
    db.delete(image)
    db.commit()


@app.get("/images/{image_id}/labels", response_model=list[schemas.LabelOut])
def list_labels(image_id: int, db: Session = Depends(get_db)):
    image = db.get(models.Image, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    return image.labels


@app.post("/images/{image_id}/labels", response_model=schemas.LabelOut)
def add_label(image_id: int, payload: schemas.LabelCreate, db: Session = Depends(get_db)):
    image = db.get(models.Image, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    label = models.Label(image_id=image_id, text=payload.text.strip())
    db.add(label)
    db.commit()
    db.refresh(label)
    return label


@app.post("/images/{image_id}/extract-label", response_model=schemas.LabelOut)
async def extract_label(image_id: int, db: Session = Depends(get_db)):
    image = db.get(models.Image, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    path = STORAGE_DIR / image.filename
    if not path.exists():
        raise HTTPException(404, "Image file missing")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{EXTRACTION_SERVICE_URL}/extract",
                files={"file": (image.original_name, path.read_bytes(), image.content_type)},
                timeout=30,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Extraction service unavailable: {exc}") from exc

    result = response.json()
    label = models.Label(
        image_id=image_id,
        text=result["label"],
        source="auto",
        confidence=result.get("confidence"),
    )
    db.add(label)
    db.commit()
    db.refresh(label)
    return label


@app.delete("/labels/{label_id}", status_code=204)
def delete_label(label_id: int, db: Session = Depends(get_db)):
    label = db.get(models.Label, label_id)
    if not label:
        raise HTTPException(404, "Label not found")
    db.delete(label)
    db.commit()
