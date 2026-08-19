FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Bakes the PP-OCRv5 model weights into the image at build time (importing
# main.py triggers the same PaddleOCR(...) call used at runtime). Without
# this, the first request after a cold start would trigger a ~30s model
# download — and on a network that blocks outbound traffic to unfamiliar
# hosts (the exact issue that broke the earlier vendor OCR pilot), it would
# fail outright. Building this image needs internet access; running the
# container does not.
RUN python -c "import main"

EXPOSE 9000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
