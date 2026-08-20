// Hardcoded so visitors on the deployed site never need to know or enter a
// backend URL. For local development, override by running
// localStorage.setItem("label_verifier_api_base", "http://localhost:8000")
// in the browser console, then reloading — no UI control for this
// intentionally, since it'd have no purpose for a real visitor.
const DEFAULT_API_BASE = "https://alcohol-label-verifier-vzt0.onrender.com";
// PP-OCRv5 is CPU-bound and doesn't parallelize well: benchmarking showed 2
// concurrent extractions already pushing per-request latency to ~5s (the
// hard budget from the interviews), and 4 concurrent pushing it to ~10s.
// Keeping this at 1 trades slower batch throughput for a guarantee that a
// single agent's wait time stays close to the ~2.5s single-request latency.
const MAX_CONCURRENT_VERIFICATIONS = 1;

const FIELD_LABELS = {
  brand_name: "Brand name",
  class_type: "Class / type",
  alcohol_content: "Alcohol content",
  net_contents: "Net contents",
  government_warning: "Government warning",
  sulfite_declaration: "Sulfite declaration",
  name_address: "Name and address",
};

const STATUS_LABELS = {
  pass: "PASS",
  fail: "FAIL",
  needs_review: "NEEDS REVIEW",
  match: "Match",
  mismatch: "Mismatch",
  review: "Review",
  not_found: "Not found on label",
  not_applicable: "N/A",
};

const BEVERAGE_LABELS = {
  distilled_spirits: "Distilled spirits",
  wine: "Wine",
  beer: "Beer / malt beverage",
};

const connectionBanner = document.getElementById("connectionBanner");
const connectionStatus = document.getElementById("connectionStatus");

const fileInput = document.getElementById("fileInput");
const fileDropText = document.getElementById("fileDropText");
const fileListEl = document.getElementById("fileList");
const addForm = document.getElementById("addForm");
const beverageTypeInput = document.getElementById("beverageType");
const brandNameInput = document.getElementById("brandName");
const classTypeInput = document.getElementById("classType");
const alcoholContentInput = document.getElementById("alcoholContent");
const alcoholContentLabel = document.getElementById("alcoholContentLabel");
const netContentsInput = document.getElementById("netContents");
const nameAddressInput = document.getElementById("nameAddress");

const queueWrap = document.getElementById("queueWrap");
const queueCount = document.getElementById("queueCount");
const queueList = document.getElementById("queueList");
const verifyAllBtn = document.getElementById("verifyAllBtn");
const addFormSubmit = document.getElementById("addFormSubmit");
const cancelEditBtn = document.getElementById("cancelEditBtn");

const resultsEmpty = document.getElementById("resultsEmpty");
const resultsList = document.getElementById("resultsList");

const detailOverlay = document.getElementById("detailOverlay");
const detailContent = document.getElementById("detailContent");
const closeDetailBtn = document.getElementById("closeDetail");

let queue = [];
let queueIdCounter = 0;
let editingId = null;

function getApiBase() {
  return localStorage.getItem("label_verifier_api_base") || DEFAULT_API_BASE;
}

// A random ID generated once per browser so one visitor's queue/results
// aren't visible to another — there's a single shared backend database and
// no login, so without this every visitor would see everyone else's
// applications (which is exactly what happened during testing). Not real
// auth, just enough to stop visitors from tripping over each other's data.
function getSessionId() {
  let id = localStorage.getItem("label_verifier_session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("label_verifier_session_id", id);
  }
  return id;
}

function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}), "X-Session-Id": getSessionId() };
  return fetch(`${getApiBase()}${path}`, { ...options, headers });
}

fileInput.addEventListener("change", () => {
  renderSelectedFiles();
});

function renderSelectedFiles() {
  const files = Array.from(fileInput.files);
  fileDropText.textContent =
    files.length === 0
      ? "Choose one or more label photos (JPEG or PNG)"
      : `${files.length} photo${files.length > 1 ? "s" : ""} selected`;
  fileListEl.innerHTML = files.map((f) => `<li>${escapeHtml(f.name)}</li>`).join("");
}

function updateAlcoholContentField() {
  // TTB does not federally require an ABV statement on beer/malt beverage
  // labels, so make the field optional when that's selected.
  const isBeer = beverageTypeInput.value === "beer";
  alcoholContentInput.required = !isBeer;
  alcoholContentLabel.textContent = isBeer ? "Alcohol content (optional for beer)" : "Alcohol content";
}

beverageTypeInput.addEventListener("change", updateAlcoholContentField);
updateAlcoholContentField();

// The backend is on a free tier that spins down when idle, so a visitor's
// first request of the day can take ~30s to wake it up. Rather than let
// that look like a silent failure, show a plain-language banner while
// waiting and retry a few times before giving up. Once connected, a small
// persistent label stays visible so the connection status is never a
// mystery, and a periodic re-check keeps it accurate if the backend spins
// down again mid-session.
async function checkHealth() {
  showConnectionBanner("Connecting to the server — this can take up to 30 seconds if it's your first visit in a while…");
  setConnectionStatus(null);

  const maxAttempts = 6;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const res = await fetch(`${getApiBase()}/health`);
      if (res.ok) {
        hideConnectionBanner();
        setConnectionStatus(true);
        return;
      }
    } catch {
      // keep retrying below
    }
    if (attempt < maxAttempts) {
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
  }

  showConnectionBanner("Having trouble reaching the server. Try refreshing the page in a moment.");
  setConnectionStatus(false);
}

function setConnectionStatus(connected) {
  if (connected === true) {
    connectionStatus.textContent = "● Connected to server";
    connectionStatus.className = "connection-status ok";
  } else if (connected === false) {
    connectionStatus.textContent = "● Disconnected";
    connectionStatus.className = "connection-status err";
  } else {
    connectionStatus.textContent = "";
    connectionStatus.className = "connection-status";
  }
}

function showConnectionBanner(message) {
  connectionBanner.textContent = message;
  connectionBanner.classList.remove("hidden");
}

function hideConnectionBanner() {
  connectionBanner.classList.add("hidden");
}

// Keeps the status label accurate through a long session — the free-tier
// backend can spin back down after ~15 minutes idle even after a
// successful connection earlier in the visit.
setInterval(async () => {
  try {
    const res = await fetch(`${getApiBase()}/health`);
    setConnectionStatus(res.ok);
  } catch {
    setConnectionStatus(false);
  }
}, 60000);

// --- Queue ---

addForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const files = Array.from(fileInput.files);
  if (files.length === 0) return;

  const fields = {
    files,
    beverage_type: beverageTypeInput.value,
    brand_name: brandNameInput.value.trim(),
    class_type: classTypeInput.value.trim(),
    alcohol_content: alcoholContentInput.value.trim(),
    net_contents: netContentsInput.value.trim(),
    name_address: nameAddressInput.value.trim(),
  };

  if (editingId !== null) {
    const item = queue.find((q) => q.id === editingId);
    if (item) Object.assign(item, fields);
    stopEditing();
  } else {
    queue.push({ id: ++queueIdCounter, status: "queued", ...fields });
  }

  addForm.reset();
  renderSelectedFiles();
  updateAlcoholContentField();
  renderQueue();
});

function startEditQueueItem(item) {
  editingId = item.id;

  beverageTypeInput.value = item.beverage_type;
  brandNameInput.value = item.brand_name;
  classTypeInput.value = item.class_type;
  alcoholContentInput.value = item.alcohol_content;
  netContentsInput.value = item.net_contents;
  nameAddressInput.value = item.name_address;
  updateAlcoholContentField();

  // File inputs can't be assigned a plain array of File objects directly,
  // but a DataTransfer's .files list can be — this is the standard way to
  // programmatically repopulate one.
  const dt = new DataTransfer();
  item.files.forEach((f) => dt.items.add(f));
  fileInput.files = dt.files;
  renderSelectedFiles();

  addFormSubmit.textContent = "Save changes";
  cancelEditBtn.classList.remove("hidden");
  renderQueue();
  addForm.scrollIntoView({ behavior: "smooth", block: "start" });
}

function stopEditing() {
  editingId = null;
  addFormSubmit.textContent = "Add to queue";
  cancelEditBtn.classList.add("hidden");
}

cancelEditBtn.addEventListener("click", () => {
  stopEditing();
  addForm.reset();
  renderSelectedFiles();
  updateAlcoholContentField();
  renderQueue();
});

function renderQueue() {
  queueWrap.classList.toggle("hidden", queue.length === 0);
  queueCount.textContent = queue.length;
  queueList.innerHTML = "";

  for (const item of queue) {
    const li = document.createElement("li");
    li.className = `queue-item queue-${item.status}${item.id === editingId ? " queue-editing" : ""}`;
    const photoCount = `${item.files.length} photo${item.files.length > 1 ? "s" : ""}`;
    li.innerHTML = `
      <span class="queue-name">${escapeHtml(item.brand_name)} <span class="queue-photo-count">(${photoCount})</span></span>
      <span class="queue-status">${queueStatusLabel(item.status)}</span>
      ${item.status === "queued" ? `
        <span class="queue-actions">
          <button class="btn btn-ghost queue-edit" data-id="${item.id}">Edit</button>
          <button class="btn btn-ghost queue-remove" data-id="${item.id}">Remove</button>
        </span>
      ` : ""}
    `;
    if (item.status === "queued") {
      li.querySelector(".queue-edit").addEventListener("click", () => startEditQueueItem(item));
      li.querySelector(".queue-remove").addEventListener("click", () => {
        queue = queue.filter((q) => q.id !== item.id);
        if (item.id === editingId) {
          stopEditing();
          addForm.reset();
          renderSelectedFiles();
          updateAlcoholContentField();
        }
        renderQueue();
      });
    }
    queueList.appendChild(li);
  }
}

function queueStatusLabel(status) {
  switch (status) {
    case "queued":
      return "Waiting";
    case "processing":
      return "Verifying…";
    case "done":
      return "Done";
    case "error":
      return "Failed";
    default:
      return status;
  }
}

verifyAllBtn.addEventListener("click", async () => {
  const pending = queue.filter((q) => q.status === "queued");
  if (pending.length === 0) return;

  verifyAllBtn.disabled = true;
  verifyAllBtn.textContent = "Verifying…";

  let index = 0;
  async function worker() {
    while (index < pending.length) {
      const item = pending[index++];
      item.status = "processing";
      renderQueue();
      try {
        await submitApplication(item);
        item.status = "done";
      } catch {
        item.status = "error";
      }
      renderQueue();
    }
  }

  const workers = Array.from(
    { length: Math.min(MAX_CONCURRENT_VERIFICATIONS, pending.length) },
    worker
  );
  await Promise.all(workers);

  verifyAllBtn.disabled = false;
  verifyAllBtn.textContent = "Verify all";
  queue = queue.filter((q) => q.status !== "done");
  renderQueue();
  loadResults();
});

async function submitApplication(item) {
  const formData = new FormData();
  for (const file of item.files) {
    formData.append("files", file);
  }
  formData.append("beverage_type", item.beverage_type);
  formData.append("brand_name", item.brand_name);
  formData.append("class_type", item.class_type);
  formData.append("alcohol_content", item.alcohol_content);
  formData.append("net_contents", item.net_contents);
  formData.append("name_address", item.name_address);

  const res = await apiFetch("/applications", { method: "POST", body: formData });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// --- Results ---

async function loadResults() {
  try {
    const res = await apiFetch("/applications");
    if (!res.ok) throw new Error();
    const applications = await res.json();
    renderResults(applications);
  } catch {
    resultsEmpty.classList.remove("hidden");
    resultsList.classList.add("hidden");
    resultsEmpty.querySelector("p").textContent = "Could not load results. Try refreshing the page.";
  }
}

function renderResults(applications) {
  const hasResults = applications.length > 0;
  resultsEmpty.classList.toggle("hidden", hasResults);
  resultsList.classList.toggle("hidden", !hasResults);
  resultsList.innerHTML = "";

  for (const app of applications) {
    const li = document.createElement("li");
    li.className = "result-item";
    const photoCount = app.images.length;
    li.innerHTML = `
      <span class="status-badge status-${app.overall_status}">${STATUS_LABELS[app.overall_status]}</span>
      <span class="result-name">${escapeHtml(app.brand_name)}</span>
      <span class="result-subtitle">${escapeHtml(app.class_type)} · ${BEVERAGE_LABELS[app.beverage_type] || app.beverage_type} · ${photoCount} photo${photoCount === 1 ? "" : "s"}</span>
    `;
    li.addEventListener("click", () => openDetail(app.id));
    resultsList.appendChild(li);
  }
}

async function openDetail(id) {
  const res = await apiFetch(`/applications/${id}`);
  const app = await res.json();
  renderDetail(app);
  detailOverlay.classList.remove("hidden");
}

function renderDetail(app) {
  const rows = app.results
    .map((r) => {
      const canOverride = r.status !== "not_applicable";
      const notApplicable = r.status === "not_applicable";
      const foundCell = notApplicable
        ? "<em>not required for this beverage type</em>"
        : r.extracted_value
        ? escapeHtml(r.extracted_value)
        : "<em>none found</em>";
      return `
        <tr class="field-row status-${r.status}">
          <td>${FIELD_LABELS[r.field_name] || r.field_name}</td>
          <td>${notApplicable ? "<em>—</em>" : escapeHtml(r.submitted_value)}</td>
          <td>${foundCell}</td>
          <td><span class="status-pill status-${r.status}">${STATUS_LABELS[r.status]}</span>${r.agent_override ? ' <span class="override-tag">agent reviewed</span>' : ""}</td>
          <td>
            ${canOverride ? `
              <button class="btn btn-ghost override-btn${r.status === "match" ? " override-active" : ""}" data-id="${r.id}" data-status="match">Mark match</button>
              <button class="btn btn-ghost override-btn${r.status === "mismatch" ? " override-active" : ""}" data-id="${r.id}" data-status="mismatch">Mark mismatch</button>
            ` : ""}
          </td>
        </tr>
      `;
    })
    .join("");

  detailContent.innerHTML = `
    <div class="detail-header">
      <div>
        <span class="status-badge status-${app.overall_status}">${STATUS_LABELS[app.overall_status]}</span>
        <span class="beverage-tag">${BEVERAGE_LABELS[app.beverage_type] || app.beverage_type}</span>
        <h3>${escapeHtml(app.brand_name)}</h3>
        <p class="hint">${escapeHtml(app.class_type)}</p>
      </div>
      <button id="deleteApp" class="btn btn-danger-ghost" data-id="${app.id}">Delete</button>
    </div>
    <div class="detail-image-gallery">
      ${app.images
        .map(
          (img) => `
        <figure class="detail-image-item">
          <img src="${getApiBase()}/applications/${app.id}/images/${img.id}/file" alt="${escapeHtml(img.original_name)}" />
          <figcaption>${escapeHtml(img.original_name)}</figcaption>
        </figure>
      `
        )
        .join("")}
    </div>
    <table class="field-table">
      <thead>
        <tr><th>Field</th><th>Submitted</th><th>Found on label</th><th>Status</th><th></th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <details class="debug-console">
      <summary>Debug: raw OCR text</summary>
      <pre>${app.ocr_text ? escapeHtml(app.ocr_text) : "<em>(no text extracted)</em>"}</pre>
    </details>
  `;

  detailContent.querySelectorAll(".override-btn").forEach((btn) => {
    btn.addEventListener("click", () => overrideResult(app.id, btn.dataset.id, btn.dataset.status));
  });

  document.getElementById("deleteApp").addEventListener("click", () => deleteApplication(app.id));
}

async function overrideResult(applicationId, resultId, status) {
  await apiFetch(`/applications/${applicationId}/results/${resultId}/override`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  openDetail(applicationId);
  loadResults();
}

async function deleteApplication(id) {
  await apiFetch(`/applications/${id}`, { method: "DELETE" });
  detailOverlay.classList.add("hidden");
  loadResults();
}

closeDetailBtn.addEventListener("click", () => detailOverlay.classList.add("hidden"));
detailOverlay.addEventListener("click", (e) => {
  if (e.target === detailOverlay) detailOverlay.classList.add("hidden");
});

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

checkHealth();
loadResults();
