import os
import uuid
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, UploadFile
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


def get_session_id(x_session_id: str = Header(...)) -> str:
    if not x_session_id.strip():
        raise HTTPException(400, "X-Session-Id header is required")
    return x_session_id


def _get_owned_application(
    db: Session, application_id: int, session_id: str
) -> models.Application:
    application = db.get(models.Application, application_id)
    if not application or application.session_id != session_id:
        raise HTTPException(404, "Application not found")
    return application


def _apply_results(db: Session, application: models.Application, ocr_text: str) -> None:
    application.ocr_text = ocr_text

    fields = {
        verification.FIELD_BRAND_NAME: application.brand_name,
        verification.FIELD_CLASS_TYPE: application.class_type,
        verification.FIELD_ALCOHOL_CONTENT: application.alcohol_content,
        verification.FIELD_NET_CONTENTS: application.net_contents,
        verification.FIELD_NAME_ADDRESS: application.name_address,
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


# TTB's own COLA guidance: upload each distinct label surface (front, back,
# neck, strip) as a separate JPEG/PNG file rather than combining them into
# one image — matches what testing found too (a combined front+back photo
# jumbled OCR line order badly enough to break the government warning
# match).
ACCEPTED_IMAGE_TYPES = {"image/jpeg", "image/png"}


@app.post("/applications", response_model=schemas.ApplicationDetailOut)
async def create_application(
    files: list[UploadFile],
    beverage_type: str = Form(...),
    brand_name: str = Form(...),
    class_type: str = Form(...),
    alcohol_content: str = Form(""),
    net_contents: str = Form(...),
    name_address: str = Form(...),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    if beverage_type not in verification.BEVERAGE_TYPES:
        raise HTTPException(400, f"beverage_type must be one of {verification.BEVERAGE_TYPES}")

    if not alcohol_content.strip() and beverage_type != verification.BEVERAGE_BEER:
        raise HTTPException(400, "alcohol_content is required for this beverage type")

    if not files:
        raise HTTPException(400, "At least one label image is required")

    for f in files:
        if f.content_type not in ACCEPTED_IMAGE_TYPES:
            raise HTTPException(400, f"{f.filename}: only JPEG or PNG images are accepted")

    saved_images: list[models.LabelImage] = []
    ocr_sections: list[str] = []

    async with httpx.AsyncClient() as client:
        for index, f in enumerate(files, start=1):
            ext = Path(f.filename or "").suffix
            stored_name = f"{uuid.uuid4().hex}{ext}"
            dest = STORAGE_DIR / stored_name
            contents = await f.read()
            dest.write_bytes(contents)

            try:
                response = await client.post(
                    f"{EXTRACTION_SERVICE_URL}/extract",
                    files={"file": (f.filename or stored_name, contents, f.content_type)},
                    timeout=30,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                for saved in saved_images:
                    (STORAGE_DIR / saved.filename).unlink(missing_ok=True)
                dest.unlink(missing_ok=True)
                raise HTTPException(502, f"Extraction service unavailable: {exc}") from exc

            label = f.filename or f"Photo {index}"
            ocr_sections.append(f"=== {label} ===\n{response.json()['text']}")
            saved_images.append(
                models.LabelImage(
                    filename=stored_name,
                    original_name=f.filename or stored_name,
                    content_type=f.content_type,
                )
            )

    ocr_text = "\n\n".join(ocr_sections)

    application = models.Application(
        session_id=session_id,
        beverage_type=beverage_type,
        brand_name=brand_name,
        class_type=class_type,
        alcohol_content=alcohol_content,
        net_contents=net_contents,
        name_address=name_address,
    )
    application.images = saved_images
    db.add(application)
    db.flush()

    _apply_results(db, application, ocr_text)

    db.commit()
    db.refresh(application)
    return application


@app.get("/applications", response_model=list[schemas.ApplicationOut])
def list_applications(
    db: Session = Depends(get_db), session_id: str = Depends(get_session_id)
):
    return (
        db.query(models.Application)
        .filter(models.Application.session_id == session_id)
        .order_by(models.Application.created_at.desc())
        .all()
    )


@app.get("/applications/{application_id}", response_model=schemas.ApplicationDetailOut)
def get_application(
    application_id: int,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    return _get_owned_application(db, application_id, session_id)


@app.get("/applications/{application_id}/images/{image_id}/file")
def get_label_image_file(application_id: int, image_id: int, db: Session = Depends(get_db)):
    # Deliberately not session-scoped like the other endpoints: this is
    # loaded via a plain <img src>, which can't attach a custom header, and
    # a query-string token isn't a great alternative (ends up in browser
    # history/server logs). The list/detail/delete endpoints below — the
    # ones that actually expose whose data is whose — are scoped; a bare
    # image file requires already knowing its exact numeric ID, which isn't
    # discoverable without session access to the parent application first.
    image = db.get(models.LabelImage, image_id)
    if not image or image.application_id != application_id:
        raise HTTPException(404, "Image not found")
    path = STORAGE_DIR / image.filename
    if not path.exists():
        raise HTTPException(404, "Image file missing")
    return FileResponse(path, media_type=image.content_type)


@app.delete("/applications/{application_id}", status_code=204)
def delete_application(
    application_id: int,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    application = _get_owned_application(db, application_id, session_id)
    for image in application.images:
        path = STORAGE_DIR / image.filename
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
    session_id: str = Depends(get_session_id),
):
    if payload.status not in (verification.STATUS_MATCH, verification.STATUS_MISMATCH):
        raise HTTPException(400, "status must be 'match' or 'mismatch'")

    application = _get_owned_application(db, application_id, session_id)

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
