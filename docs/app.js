const DEFAULT_API_BASE = "http://localhost:8000";
const MAX_CONCURRENT_VERIFICATIONS = 4;

const FIELD_LABELS = {
  brand_name: "Brand name",
  class_type: "Class / type",
  alcohol_content: "Alcohol content",
  net_contents: "Net contents",
  government_warning: "Government warning",
};

const STATUS_LABELS = {
  pass: "PASS",
  fail: "FAIL",
  needs_review: "NEEDS REVIEW",
  match: "Match",
  mismatch: "Mismatch",
  review: "Review",
  not_found: "Not found on label",
};

const apiBaseInput = document.getElementById("apiBase");
const apiStatus = document.getElementById("apiStatus");

const fileInput = document.getElementById("fileInput");
const fileDropText = document.getElementById("fileDropText");
const addForm = document.getElementById("addForm");
const brandNameInput = document.getElementById("brandName");
const classTypeInput = document.getElementById("classType");
const alcoholContentInput = document.getElementById("alcoholContent");
const netContentsInput = document.getElementById("netContents");

const queueWrap = document.getElementById("queueWrap");
const queueCount = document.getElementById("queueCount");
const queueList = document.getElementById("queueList");
const verifyAllBtn = document.getElementById("verifyAllBtn");

const resultsEmpty = document.getElementById("resultsEmpty");
const resultsList = document.getElementById("resultsList");

const detailOverlay = document.getElementById("detailOverlay");
const detailContent = document.getElementById("detailContent");
const closeDetailBtn = document.getElementById("closeDetail");

let queue = [];
let queueIdCounter = 0;

function getApiBase() {
  return localStorage.getItem("label_verifier_api_base") || DEFAULT_API_BASE;
}

function setApiBase(url) {
  localStorage.setItem("label_verifier_api_base", url.replace(/\/$/, ""));
}

apiBaseInput.value = getApiBase();

document.getElementById("saveApiBase").addEventListener("click", () => {
  setApiBase(apiBaseInput.value.trim() || DEFAULT_API_BASE);
  apiBaseInput.value = getApiBase();
  checkHealth();
  loadResults();
});

fileInput.addEventListener("change", () => {
  fileDropText.textContent = fileInput.files[0]?.name || "Choose a label photo, or drop it here";
});

async function checkHealth() {
  try {
    const res = await fetch(`${getApiBase()}/health`);
    if (!res.ok) throw new Error();
    apiStatus.textContent = "connected";
    apiStatus.className = "status ok";
  } catch {
    apiStatus.textContent = "unreachable";
    apiStatus.className = "status err";
  }
}

// --- Queue ---

addForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;

  queue.push({
    id: ++queueIdCounter,
    file,
    brand_name: brandNameInput.value.trim(),
    class_type: classTypeInput.value.trim(),
    alcohol_content: alcoholContentInput.value.trim(),
    net_contents: netContentsInput.value.trim(),
    status: "queued",
  });

  addForm.reset();
  fileDropText.textContent = "Choose a label photo, or drop it here";
  renderQueue();
});

function renderQueue() {
  queueWrap.classList.toggle("hidden", queue.length === 0);
  queueCount.textContent = queue.length;
  queueList.innerHTML = "";

  for (const item of queue) {
    const li = document.createElement("li");
    li.className = `queue-item queue-${item.status}`;
    li.innerHTML = `
      <span class="queue-name">${escapeHtml(item.brand_name)}</span>
      <span class="queue-status">${queueStatusLabel(item.status)}</span>
      ${item.status === "queued" ? `<button class="btn btn-ghost queue-remove" data-id="${item.id}">Remove</button>` : ""}
    `;
    if (item.status === "queued") {
      li.querySelector(".queue-remove").addEventListener("click", () => {
        queue = queue.filter((q) => q.id !== item.id);
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
  formData.append("file", item.file);
  formData.append("brand_name", item.brand_name);
  formData.append("class_type", item.class_type);
  formData.append("alcohol_content", item.alcohol_content);
  formData.append("net_contents", item.net_contents);

  const res = await fetch(`${getApiBase()}/applications`, { method: "POST", body: formData });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// --- Results ---

async function loadResults() {
  try {
    const res = await fetch(`${getApiBase()}/applications`);
    if (!res.ok) throw new Error();
    const applications = await res.json();
    renderResults(applications);
  } catch {
    resultsEmpty.classList.remove("hidden");
    resultsList.classList.add("hidden");
    resultsEmpty.querySelector("p").textContent = "Could not load results. Check the API base URL above.";
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
    li.innerHTML = `
      <span class="status-badge status-${app.overall_status}">${STATUS_LABELS[app.overall_status]}</span>
      <span class="result-name">${escapeHtml(app.brand_name)}</span>
      <span class="result-subtitle">${escapeHtml(app.class_type)}</span>
    `;
    li.addEventListener("click", () => openDetail(app.id));
    resultsList.appendChild(li);
  }
}

async function openDetail(id) {
  const res = await fetch(`${getApiBase()}/applications/${id}`);
  const app = await res.json();
  renderDetail(app);
  detailOverlay.classList.remove("hidden");
}

function renderDetail(app) {
  const rows = app.results
    .map((r) => {
      const canOverride = r.status !== "match";
      return `
        <tr class="field-row status-${r.status}">
          <td>${FIELD_LABELS[r.field_name] || r.field_name}</td>
          <td>${escapeHtml(r.submitted_value)}</td>
          <td>${r.extracted_value ? escapeHtml(r.extracted_value) : "<em>none found</em>"}</td>
          <td><span class="status-pill status-${r.status}">${STATUS_LABELS[r.status]}</span>${r.agent_override ? ' <span class="override-tag">agent reviewed</span>' : ""}</td>
          <td>
            ${canOverride ? `
              <button class="btn btn-ghost override-btn" data-id="${r.id}" data-status="match">Mark match</button>
              <button class="btn btn-ghost override-btn" data-id="${r.id}" data-status="mismatch">Mark mismatch</button>
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
        <h3>${escapeHtml(app.brand_name)}</h3>
        <p class="hint">${escapeHtml(app.class_type)}</p>
      </div>
      <button id="deleteApp" class="btn btn-danger-ghost" data-id="${app.id}">Delete</button>
    </div>
    <img class="detail-image" src="${getApiBase()}/applications/${app.id}/image" alt="${escapeHtml(app.brand_name)}" />
    <table class="field-table">
      <thead>
        <tr><th>Field</th><th>Submitted</th><th>Found on label</th><th>Status</th><th></th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  detailContent.querySelectorAll(".override-btn").forEach((btn) => {
    btn.addEventListener("click", () => overrideResult(app.id, btn.dataset.id, btn.dataset.status));
  });

  document.getElementById("deleteApp").addEventListener("click", () => deleteApplication(app.id));
}

async function overrideResult(applicationId, resultId, status) {
  await fetch(`${getApiBase()}/applications/${applicationId}/results/${resultId}/override`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  openDetail(applicationId);
  loadResults();
}

async function deleteApplication(id) {
  await fetch(`${getApiBase()}/applications/${id}`, { method: "DELETE" });
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
