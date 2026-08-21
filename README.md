# Label Verifier

An AI-powered prototype for verifying alcohol label artwork against the values submitted on a TTB label application — brand name, class/type, alcohol content, net contents, name/address of the bottler or producer, and the mandatory government warning statement. Verification rules adapt to the beverage category (distilled spirits, wine, or beer/malt beverage), since TTB's requirements genuinely differ between them.

Built as a standalone proof-of-concept, not integrated with COLA. Monorepo layout:

- `docs/` — Static frontend (plain HTML/CSS/JS, no build step). Served via GitHub Pages.
- `backend/` — Owns applications and verification results (SQLite + local image storage). Runs the field-matching/compliance logic. Calls the extraction service for OCR.
- `extraction-service/` — Small dedicated API that OCRs a label image and returns raw text. Runs PaddleOCR (PP-OCRv5).

```
frontend (docs)  --->  backend (:8000)  --->  extraction-service (:9000)
                          |                       (PaddleOCR / PP-OCRv5)
                        SQLite + local image storage
```

## Approach

An agent selects a beverage type, enters what was submitted on the application (brand name, class/type, alcohol content, net contents), and uploads one photo per label surface — front, back, neck, strip, however many the physical container actually has, matching TTB's own COLA guidance to submit each surface as a separate file rather than a combined image. The backend OCRs each photo separately and pools the results before checking each field:

- **Brand name / class-type**: normalized (lowercased, punctuation stripped) and fuzzy-matched against the label text. An exact match after normalization → `match`. A close-but-imperfect match → `needs review`, surfaced to the agent rather than silently passed or failed — this is what makes "Stone's Throw" vs "STONE'S THROW" pass automatically while still flagging genuinely uncertain cases for a human. Because this searches for the expected value *anywhere* in the OCR text rather than assuming a fixed layout, it's naturally tolerant of **personalized labels** — extra custom text (a gift message, an event name) on the label doesn't interfere with matching the mandatory fields, since nothing requires the label to contain *only* those fields. Verified directly with a synthetic personalized label during development.
- **Alcohol content**: category-dependent, since this is where TTB's rules diverge most:
  - *Distilled spirits*: always mandatory, matched against the OCR'd percentage with a tight tolerance.
  - *Wine*: mandatory, but 27 CFR 4.36 grants a wider legal tolerance (±1.5 points under 14% ABV, ±1.0 at/above it — fortified wines). Wines in the 7–14% band may also state "Table Wine" or "Light Wine" instead of a percentage; the check accepts either.
  - *Beer*: TTB does **not** federally require an ABV statement on malt beverage labels. If the agent leaves the field blank, it's marked `not applicable` rather than flagged as missing. If a value is provided (common practice, sometimes state-mandated), it's still verified.
- **Net contents**: volume + unit parsed and compared, unit-normalized so "12 FL OZ", "fl. oz.", and "floz" all match — the standard beer unit, which differs from the mL/L convention on wine and spirits.
- **Name and address of the bottler/producer**: required on "any label" across all three categories per TTB (no conditional logic needed, unlike the fields discussed below), fuzzy-matched the same way as brand name/class-type.
- **Government warning**: checked with an exact, case-sensitive substring match against the legally required text (27 CFR 16.21). Because OCR preserves case as printed, this alone catches "Government Warning" in title case or reworded text — no font-weight/bold detection needed. This applies identically to all three categories and has no tolerance: if it doesn't match exactly, the application fails regardless of how well everything else matches.
- **Sulfite declaration** (wine and malt beverages): products with ≥10ppm sulfur dioxide must carry a "Contains Sulfites" statement (27 CFR 4.32(e) for wine; TTB's malt beverage manual lists the same disclosure "if applicable"). Since the label alone can't confirm whether the product actually contains that much, a missing statement is surfaced for agent review rather than an automatic fail.
- **Alcohol content format**: independent of whether the percentage itself matches, TTB requires the statement to read "Alcohol ___% by volume" or "___% Alc./Vol." — "ABV" is explicitly disallowed as an abbreviation. A label using "ABV" gets its alcohol-content result downgraded to `needs review` even if the number is exactly right, with a note explaining why — this is a format defect, not an uncertain match, so it's a different kind of flag than the fuzzy-matching `review` status above, but reuses the same status for simplicity.

An agent can override any non-matching field with "Mark match" / "Mark mismatch" — encoding the judgment call that fuzzy matching alone can't make, while leaving the government warning's exact-match requirement absolute (an override on it still recomputes correctly, but its own status still governs whether the application can ever show as fully verified without agent action). Both buttons stay available after an override, so an agent can freely change their mind in either direction — an earlier version hid them inconsistently (only once a field was marked `match`, never after `mismatch`), which made a mistaken override effectively permanent; caught by a real user during testing. Fields marked `not applicable` (e.g. blank beer ABV) aren't override candidates — there's nothing to judge.

Batch upload is a client-side queue: an agent adds several label+field entries before running verification, which then fires against the same single-item endpoint (currently one at a time — see the OCR engine benchmark below for why). Any queued entry — fields and photos both — can be edited or removed up until it's verified, so a data-entry mistake doesn't mean deleting the entry and starting over. This was a deliberate simplification over a server-side job queue — see Trade-offs below.

Entries can also be bulk-added from a CSV (`beverage_type,brand_name,class_type,alcohol_content,net_contents,name_address,images` — a downloadable template is linked in the UI), alongside a multi-select of the photos it references; each row's `images` column is a semicolon-separated list of filenames matched case-insensitively against the selected photos, so one photo picker covers every row at once rather than re-selecting files per label. Chose CSV over JSON here since it's the more likely real-world source for this — an agent exporting a spreadsheet of pending applications, not hand-authoring JSON — and it still handles the multi-photo-per-label case fine via that delimited list, just not as cleanly as a JSON array would. Parsing is hand-rolled (no dependency) but RFC 4180-correct — quoted fields, embedded commas and quotes — since name/address values routinely contain commas ("Old Tom Distillery, Frankfort, KY") that a naive `split(",")` would corrupt. A bad row (unrecognized beverage type, missing required field, a referenced photo that wasn't selected) is skipped with a specific per-row error rather than failing the whole import, so one typo in a 200-row file doesn't block the other 199.

## Tools & technical choices

- **FastAPI + SQLite** for the backend — no real concurrency/scale requirements for a prototype, and SQLite keeps setup to zero external dependencies.
- **PaddleOCR / PP-OCRv5 (local OCR)**, not a cloud vision API — still satisfies the interviews' firewall constraint (no outbound calls at request time; see the Dockerfile note below on baking model weights in at build time instead) while handling the stylized fonts and decorative layouts real labels use far better than a classical OCR engine. See the benchmark writeup just below — this replaced an initial Tesseract-based implementation after side-by-side testing showed it silently missing fields on real label photos.
- **Vanilla HTML/CSS/JS frontend**, no framework or build step. Matches the "my 73-year-old mother could use it" requirement more directly than a heavier SPA would, and it's what lets the frontend be served as-is from GitHub Pages.
- **Two-service split** (backend vs. extraction-service) so the OCR/perception layer stays swappable — this is what let the engine get replaced without touching any compliance/matching logic, just the `/extract` implementation behind it.

## OCR engine: Tesseract → PaddleOCR (PP-OCRv5)

The prototype originally used Tesseract. Testing against a real label photo (a photographed spirits label, front and back) surfaced a real problem: Tesseract silently missed the alcohol content and net contents entirely — not misread, just absent from the output — apparently due to the label's stylized bold serif brand typography. Both fields required a manual agent override every time.

Benchmarked PP-OCRv5 against the same photo before committing to the swap:

| | Tesseract | PP-OCRv5 (mobile det+rec) |
|---|---|---|
| Brand / class-type / warning | ✅ read | ✅ read |
| Alcohol content ("45% ALC/VOL") | ❌ missing entirely | ✅ read correctly |
| Net contents ("750 ML") | ❌ missing entirely | ✅ read correctly |
| Same label, rotated 8° | Output mostly unreadable garbage | Still substantially correct — a few individual character swaps, all key fields intact |
| Single-image latency | ~0.3s | ~2.1–2.8s |

PP-OCRv5 also directly helps with **personalized labels**: since the matching logic searches for expected values anywhere in the OCR text rather than assuming a fixed layout (see Approach above), better raw text extraction on artistic/decorative labels flows straight through to better field matching, with no changes needed elsewhere.

**One-image-per-label-surface turned out to matter more than expected.** Testing against a real photographed label that had front and back panels side-by-side in a single combined image, the government warning came back as a mismatch — not because OCR misread it, but because reading the image left-to-right interleaved text from the front panel into the middle of the back panel's warning paragraph ("SINGLE BARREL" and "STRAIGHT RYE" showed up mid-sentence). Splitting the same content into two separate photos and submitting both fixed it completely; the matching logic is already layout-agnostic and just needed clean per-image text rather than one photo with two spatially-separate label surfaces jammed together. This became the basis for the multi-photo upload feature described below, rather than trying to detect and separate panels within a single image — which would've been more fragile (breaks on vertically-stacked panels, rotated photos, 3+ surfaces) for no real benefit over just asking agents to upload photos the way they'd naturally take them.

### Multi-photo upload

An application can have any number of label photos — one per physical surface (front, back, neck, strip), no arbitrary cap, matching TTB's own COLA upload guidance (separate JPEG/PNG file per surface, not combined into one graphic). Each photo is OCR'd independently and the results are pooled (with a per-image header in the raw OCR debug view, e.g. `=== back_label.jpg ===`) before running the same field-matching logic — nothing about the matching itself needed to change, since it already treats the OCR text as one undifferentiated pool to search rather than depending on any single image's layout. Uploads are restricted to JPEG/PNG specifically (not just any `image/*` MIME type) to match TTB's stated accepted formats. Verified with the exact front+back scenario described above: fields that mismatched when combined into one image all matched correctly once submitted as two separate photos, and a single-photo submission (the common case) still works unchanged.

**The real cost is concurrency, not per-request speed.** A single extraction easily clears the 5-second budget. But PP-OCRv5 is CPU-bound and doesn't parallelize well on this hardware: 2 concurrent extractions already pushed per-request latency to ~5s, and 4 concurrent pushed it to ~10s — confirmed to be genuine compute contention (isolated with direct Python threading, and tried capping Paddle's internal thread count, neither changed the picture). Tesseract's classical algorithm had none of this problem. The mitigation here is blunt: the batch queue now processes one item at a time instead of four, trading slower aggregate batch throughput for keeping a single agent's wait time close to the clean single-request latency. **This concurrency benchmark predates the engine change documented just below (Paddle-native, not ONNX Runtime) and was run on a 16-core dev machine — it should be re-measured on the actual deployment target with the current engine before trusting it in production**, the same way single-request latency below turned out to need real hardware to get right. A more complete fix (worker pool sized to available cores, a dedicated inference server, or GPU acceleration) is future work, not implemented here.

**Three more issues only showed up in production, on real x86_64 hardware:**
1. Paddle's oneDNN (Intel CPU) backend defaults on and crashed the first real request with `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [...DoubleAttribute]` — a bug in this Paddle version's oneDNN path. Invisible in all local testing since oneDNN is Intel-specific and never activates on Apple Silicon.
2. The service still OOMed after that fix, on Render's 512MB tier. Local memory profiling (`psutil`, measuring RSS around the actual `_ocr.predict()` call) found the real number: model load alone takes ~850MB, and a real 12MP phone photo (4032×3024) sent through uncapped pushed peak usage to **6.2GB** — the image-size caps added earlier (`MIN_DIMENSION`) only handled small/cropped photos, never bounded large ones. Added a matching `MAX_DIMENSION=1600` downscale cap, which brought the same photo down to ~1.7GB with no visible accuracy loss. **A 512MB instance cannot run this service; budget at least ~2GB.**
3. With the crash fixed by disabling oneDNN (`enable_mkldnn=False`), a real request on Render's 1-CPU instance took **~23 seconds** — the local 16-core benchmark (~2s) turned out to depend heavily on oneDNN's optimizations, which we'd just turned off to avoid the crash. The actual fix: `engine="onnxruntime"` routes inference through ONNX Runtime instead of Paddle's native engine, sidestepping the buggy oneDNN integration entirely rather than just disabling it — and it turned out faster *and* lower-memory than either Paddle configuration in local testing (0.2–0.3s, ~1.3GB peak on the same phone-photo test). This alone brought the live number down to ~7s — better, but still over budget.
4. Tried capping thread count next (`cpu_threads=2`), which had literally zero effect (~7.1s → ~7.5s) — turned out that setting only feeds Paddle's native engine config builder and is silently skipped for `engine="onnxruntime"` unless `engine_config` is passed explicitly (see `paddleocr/_common_args.py`, `prepare_common_init_args`). The actual knob is ONNX Runtime's own `intra_op_num_threads`/`inter_op_num_threads`, passed via `engine_config={"intra_op_num_threads": 2, "inter_op_num_threads": 1}` — confirmed locally that *this* one is actually applied (it measurably changed local timing when constrained, unlike the no-op version), and on Render it brought live requests down to a consistent **~3.1s**, comfortably inside the 5-second budget.

Full end-to-end verification (upload → OCR → field matching) confirmed working on the deployed Render services as of this fix, not just in local testing.

## Assumptions & limitations

- **Scope**: verification covers the fields called out in the prompt's sample label (brand name, class/type, alcohol content, net contents, government warning) plus name/address of the bottler/producer, added after reviewing TTB's actual per-category beverage manuals (they list it as required on "any label" for all three categories, with no conditional logic needed).
- **Application data entry**: since the interviews explicitly said this prototype isn't integrating with COLA and didn't specify an intake format, applications are entered through a simple form (one per label) rather than assuming a particular upstream system.
- **Per-visitor data isolation, not real accounts.** With the app publicly deployed and multiple people testing it, a real problem surfaced: there's one shared database and originally no concept of "whose" data a row was, so every visitor's `GET /applications` returned everyone's applications — one tester saw another's results just by opening the site. Fixed with a random ID the frontend generates once per browser (`localStorage`, no login), sent as an `X-Session-Id` header and used to filter every application-scoped endpoint. This isn't authentication — nothing stops a client from sending a different ID than its own — but it fully solves the actual problem (visitors tripping over each other's test data) without building real accounts, which would be disproportionate for a prototype. The image-file endpoint is deliberately *not* scoped this way: it's loaded via a plain `<img src>`, which can't attach a custom header, and a bare image file requires already knowing its exact numeric ID, which isn't discoverable without session access to the parent application's listing first.
- **Batch processing is client-orchestrated**, not a true async job queue, and (per the OCR benchmark above) currently processes one item at a time rather than in parallel. It has live per-row status and handles tens of items fine, but at the interview's cited scale (200-300 at once) a production version would want a server-side job queue with worker concurrency actually sized to the deployment host, so a dropped connection doesn't lose in-flight work and throughput isn't limited to one label at a time.
- **Category-specific rules deliberately stop short of full TTB coverage.** Cross-referencing TTB's beverage manuals surfaced a longer list of required fields per category — Age Statement and Commodity Statement (spirits), Appellation of Origin and % Foreign Wine (wine), Country of Origin (imports, all categories), Color Ingredient Disclosures (all categories, "if applicable"). All of these were deliberately left out, not overlooked: each is genuinely conditional (only applies if the product is imported, aged, uses color additives, etc.), so implementing them as always-visible form fields would mean the form silently grows extra required fields based on hidden rules — directly at odds with the "my 73-year-old mother could use it" / "don't make my life harder" requirements that were the most emphasized design constraint in the interviews. There's a correctness risk too: several of these have real regulatory nuance (age statement rules vary by spirit class and aging period; appellation of origin ties to actual geographic accuracy) that would be easy to model shallowly under time pressure, and a superficial "does this text appear somewhere" check on something this nuanced risks giving an agent false confidence — arguably worse than the current honest, documented gap. If this scope expands later, a collapsed "Additional fields" section (closed by default, so the common case stays simple) is the natural way to add them without changing the default experience.
- **Wine ABV tolerance** uses a simplified two-band model (±1.5 under 14%, ±1.0 at/above) approximating 27 CFR 4.36 rather than every class-specific exception in the full regulation.
- **Image quality**: images are upscaled before OCR when small (this measurably fixed a cropped-photo test case during benchmarking), and PP-OCRv5's textline-orientation classifier handles moderate rotation well. There's still no perspective/deskew correction for photos taken at a sharp angle, and heavy glare will still hurt extraction — exactly the case Jenny raised. A natural extension is a hybrid mode: PP-OCRv5 first, with an optional (agent-triggered) cloud-vision retry for label photos that still fail extraction.
- **Security**: per the interview notes, no hardening was done beyond what's reasonable for a local prototype (no real auth beyond the soft per-visitor isolation described above, no encryption at rest) — flagged explicitly since a production deployment would need it.

## Future plans / next iterations

Beyond the trade-offs documented above, these are the next things worth building — not implemented here because each is a real scope expansion, not a quick add:

- **True async job queue for batch verification.** The current client-orchestrated, one-item-at-a-time queue (see "Batch processing is client-orchestrated" above) works fine for testing but won't hold up at the interview's cited scale (200-300 labels at once). A server-side job queue with worker concurrency sized to the actual deployment host would remove the one-at-a-time throughput ceiling and survive a dropped connection without losing in-flight work. CSV bulk import (see Approach above) solves getting 200-300 entries *in*; this is the other half — actually processing that many without one dropped request stalling the batch.
- **Agent accounts.** Replace the current per-visitor session isolation (a soft, no-password `X-Session-Id` — see "Per-visitor data isolation" above) with real authentication, so results persist to a specific agent's account rather than a browser's `localStorage`, and are reachable from any device rather than tied to the browser that created them.
- **UI/UX pass.** The interface so far was built to clear the "simple enough for anyone to use" bar, not polished for sustained daily use. A follow-up pass would revisit layout and information density on the results/detail views and general visual polish, now that the underlying verification logic is stable enough to build on top of without it shifting under the UI.
- **In-app TTB reference.** A quick-reference panel (or inline tooltips) explaining what each required field means, with direct links to the relevant TTB guidance (COLA, 27 CFR labeling requirements), so an agent second-guessing a flagged field doesn't have to leave the tool to look it up.

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

The frontend's backend URL is hardcoded to the deployed Render backend (`docs/app.js`, `DEFAULT_API_BASE`) so visitors never need to configure anything. For local development, override it without touching the UI: run `localStorage.setItem("label_verifier_api_base", "http://localhost:8000")` in the browser console, then reload.

The backend finds the extraction service via the `EXTRACTION_SERVICE_URL` env var, default `http://localhost:9000`.

The extraction service's first startup downloads the PP-OCRv5 model weights (~30s, one-time, cached to disk afterward) — the first request will be slow while this happens if you don't wait for it to finish first.

## Deploying

- **Frontend**: In the repo's Settings → Pages, set Source to `Deploy from a branch`, branch `main`, folder `/docs`. Live at `https://<your-username>.github.io/alcohol-label-verifier/`.
- **Backend**: deploy anywhere that runs Python (Render, Railway, Fly.io, etc.). No system dependencies required. On Render's free tier this spins down when idle, so a visitor's first request after a quiet period can take ~30s to wake it up — the frontend shows a plain-language "connecting" banner during this rather than letting it look like a silent failure, and a small persistent "Connected to server" label (re-checked every 60s) stays visible for the rest of the visit so the connection status is never a mystery.
- **Extraction service**: deploy via the included `extraction-service/Dockerfile` rather than a plain Python buildpack — it bakes the PP-OCRv5 model weights into the image at build time (`RUN python -c "import main"`), so the running container never needs outbound internet access, matching the firewall constraint from the interviews. Building the image does need internet access. Point the backend's `EXTRACTION_SERVICE_URL` at wherever this ends up. If the backend's URL changes, update `DEFAULT_API_BASE` in `docs/app.js` to match. **Needs at least ~2GB RAM** (confirmed by an actual OOM crash on a 512MB tier — see the OCR section above for the profiling numbers behind that). **Before relying on this in front of real users, re-run the concurrency benchmark from the OCR section above on the actual host** — it was measured on a 16-core dev machine, and a small cloud instance will hit the CPU contention ceiling sooner.
