from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LabelCreate(BaseModel):
    text: str


class LabelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    source: str
    confidence: float | None = None
    created_at: datetime


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_name: str
    content_type: str
    created_at: datetime


class ImageDetailOut(ImageOut):
    labels: list[LabelOut] = []
