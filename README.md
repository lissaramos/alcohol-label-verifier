# Labeler

An image labeling tool: upload images, then extract a label automatically or add one manually.

Monorepo layout:

- `docs/` — Static frontend (plain HTML/CSS/JS, no build step). Served via GitHub Pages so it's live at your `github.io` URL.
- `backend/` — Main API. Owns image storage and label records (SQLite + local files). Calls the extraction service on request.
- `extraction-service/` — Small dedicated API that takes an image and returns a label. Currently a stub that returns a placeholder result — swap `run_extraction` in `extraction-service/main.py` for the real processing logic.

```
frontend (docs)  --->  backend (:8000)  --->  extraction-service (:9000)
                          |
                        SQLite + local image storage
```

## Running everything locally

```bash
./dev.sh
```

This starts the backend (`:8000`), extraction service (`:9000`), and frontend (`:5500`) together. First time, install each service's dependencies:

```bash
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && deactivate && cd ..
cd extraction-service && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && deactivate && cd ..
```

Or run services individually:

```bash
cd backend && uvicorn main:app --reload --port 8000
cd extraction-service && uvicorn main:app --reload --port 9000
cd docs && python3 -m http.server 5500
```

The frontend's "API base URL" field (top right) points at the backend — defaults to `http://localhost:8000` and remembers your choice.

The backend finds the extraction service via the `EXTRACTION_SERVICE_URL` env var, default `http://localhost:9000`.

## Deploying

- **Frontend**: In the repo's Settings → Pages, set Source to `Deploy from a branch`, branch `main`, folder `/docs`. Live at `https://<your-username>.github.io/labeler/`.
- **Backend & extraction-service**: Deploy both somewhere reachable over HTTPS (Render, Railway, Fly.io, etc.), and set `EXTRACTION_SERVICE_URL` on the backend to the extraction service's deployed URL. Point the frontend's "API base URL" at the deployed backend.
