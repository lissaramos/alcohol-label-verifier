import os
import uuid
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

import models
import schemas
import verification
from database import Base, SessionLocal, engine, get_db

Base.metadata.create_all(bind=engine)

STORAGE_DIR = Path(__file__).parent / "storage"
STORAGE_DIR.mkdir(exist_ok=True)

EXTRACTION_SERVICE_URL = os.environ.get("EXTRACTION_SERVICE_URL", "http://localhost:9000")

app = FastAPI(title="Label Verifier API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


def _apply_results(db: Session, application: models.Application, ocr_text: str) -> None:
    application.ocr_text = ocr_text

    fields = {
        verification.FIELD_BRAND_NAME: application.brand_name,
        verification.FIELD_CLASS_TYPE: application.class_type,
        verification.FIELD_ALCOHOL_CONTENT: application.alcohol_content,
        verification.FIELD_NET_CONTENTS: application.net_contents,
    }
    field_results = verification.run_verification(fields, ocr_text, application.beverage_type)
    application.overall_status = verification.compute_overall_status(field_results)

    for field_result in field_results:
        db.add(
            models.VerificationResult(
                application_id=application.id,
                field_name=field_result.field_name,
                submitted_value=field_result.submitted_value,
                extracted_value=field_result.extracted_value,
                status=field_result.status,
                similarity=field_result.similarity,
            )
        )


@app.post("/applications", response_model=schemas.ApplicationDetailOut)
async def create_application(
    file: UploadFile,
    beverage_type: str = Form(...),
    brand_name: str = Form(...),
    class_type: str = Form(...),
    alcohol_content: str = Form(""),
    net_contents: str = Form(...),
    db: Session = Depends(get_db),
):
    if beverage_type not in verification.BEVERAGE_TYPES:
        raise HTTPException(400, f"beverage_type must be one of {verification.BEVERAGE_TYPES}")

    if not alcohol_content.strip() and beverage_type != verification.BEVERAGE_BEER:
        raise HTTPException(400, "alcohol_content is required for this beverage type")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    ext = Path(file.filename or "").suffix
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = STORAGE_DIR / stored_name

    contents = await file.read()
    dest.write_bytes(contents)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{EXTRACTION_SERVICE_URL}/extract",
                files={"file": (file.filename or stored_name, contents, file.content_type)},
                timeout=30,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(502, f"Extraction service unavailable: {exc}") from exc

    ocr_text = response.json()["text"]

    application = models.Application(
        beverage_type=beverage_type,
        brand_name=brand_name,
        class_type=class_type,
        alcohol_content=alcohol_content,
        net_contents=net_contents,
        image_filename=stored_name,
        image_original_name=file.filename or stored_name,
        image_content_type=file.content_type,
    )
    db.add(application)
    db.flush()

    _apply_results(db, application, ocr_text)

    db.commit()
    db.refresh(application)
    return application


@app.get("/applications", response_model=list[schemas.ApplicationOut])
def list_applications(db: Session = Depends(get_db)):
    return (
        db.query(models.Application)
        .order_by(models.Application.created_at.desc())
        .all()
    )


@app.get("/applications/{application_id}", response_model=schemas.ApplicationDetailOut)
def get_application(application_id: int, db: Session = Depends(get_db)):
    application = db.get(models.Application, application_id)
    if not application:
        raise HTTPException(404, "Application not found")
    return application


@app.get("/applications/{application_id}/image")
def get_application_image(application_id: int, db: Session = Depends(get_db)):
    application = db.get(models.Application, application_id)
    if not application:
        raise HTTPException(404, "Application not found")
    path = STORAGE_DIR / application.image_filename
    if not path.exists():
        raise HTTPException(404, "Image file missing")
    return FileResponse(path, media_type=application.image_content_type)


@app.delete("/applications/{application_id}", status_code=204)
def delete_application(application_id: int, db: Session = Depends(get_db)):
    application = db.get(models.Application, application_id)
    if not application:
        raise HTTPException(404, "Application not found")
    path = STORAGE_DIR / application.image_filename
    if path.exists():
        path.unlink()
    db.delete(application)
    db.commit()


@app.patch(
    "/applications/{application_id}/results/{result_id}/override",
    response_model=schemas.ApplicationDetailOut,
)
def override_result(
    application_id: int,
    result_id: int,
    payload: schemas.OverrideRequest,
    db: Session = Depends(get_db),
):
    if payload.status not in (verification.STATUS_MATCH, verification.STATUS_MISMATCH):
        raise HTTPException(400, "status must be 'match' or 'mismatch'")

    application = db.get(models.Application, application_id)
    if not application:
        raise HTTPException(404, "Application not found")

    result = db.get(models.VerificationResult, result_id)
    if not result or result.application_id != application_id:
        raise HTTPException(404, "Verification result not found")

    result.status = payload.status
    result.agent_override = True

    application.overall_status = verification.compute_overall_status(
        [
            verification.FieldResult(
                field_name=r.field_name,
                submitted_value=r.submitted_value,
                extracted_value=r.extracted_value,
                status=r.status,
                similarity=r.similarity,
            )
            for r in application.results
        ]
    )

    db.commit()
    db.refresh(application)
    return application
