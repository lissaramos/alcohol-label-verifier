from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VerificationResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    field_name: str
    submitted_value: str
    extracted_value: str | None
    status: str
    similarity: float | None
    agent_override: bool


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    brand_name: str
    class_type: str
    alcohol_content: str
    net_contents: str
    image_original_name: str
    overall_status: str
    created_at: datetime


class ApplicationDetailOut(ApplicationOut):
    ocr_text: str
    results: list[VerificationResultOut] = []


class OverrideRequest(BaseModel):
    status: str
