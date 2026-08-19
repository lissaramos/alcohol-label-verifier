from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    beverage_type: Mapped[str] = mapped_column(String, default="distilled_spirits")
    brand_name: Mapped[str] = mapped_column(String)
    class_type: Mapped[str] = mapped_column(String)
    alcohol_content: Mapped[str] = mapped_column(String)
    net_contents: Mapped[str] = mapped_column(String)

    ocr_text: Mapped[str] = mapped_column(Text, default="")
    overall_status: Mapped[str] = mapped_column(String, default="needs_review")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    images: Mapped[list["LabelImage"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="LabelImage.id",
    )
    results: Mapped[list["VerificationResult"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class LabelImage(Base):
    """One photo of one physical label surface (front, back, neck, strip,
    etc.) — TTB's own COLA guidance is to upload each surface as a separate
    file rather than combining them into one image, since a combined photo
    confused OCR line-ordering badly enough to break the government warning
    match in testing.
    """

    __tablename__ = "label_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))

    filename: Mapped[str] = mapped_column(String, unique=True)
    original_name: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String)

    application: Mapped["Application"] = relationship(back_populates="images")


class VerificationResult(Base):
    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))

    field_name: Mapped[str] = mapped_column(String)
    submitted_value: Mapped[str] = mapped_column(Text)
    extracted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String)
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_override: Mapped[bool] = mapped_column(default=False)

    application: Mapped["Application"] = relationship(back_populates="results")
