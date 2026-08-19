FROM python:3.12-slim

# OpenCV (a PaddleOCR dependency) needs these shared libraries at import
# time, which the slim base image doesn't include on its own — without them,
# `import main` below fails with a missing libGL.so.1/libgthread error.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

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
# Render (and most PaaS hosts) assign a dynamic $PORT and expect the
# container to listen on it — shell form so the variable actually expands,
# falling back to 9000 for local `docker run`.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-9000}
