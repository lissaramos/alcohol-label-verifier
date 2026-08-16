const DEFAULT_API_BASE = "http://localhost:8000";

const apiBaseInput = document.getElementById("apiBase");
const apiStatus = document.getElementById("apiStatus");
const gallery = document.getElementById("gallery");
const uploadForm = document.getElementById("uploadForm");
const fileInput = document.getElementById("fileInput");
const fileDropText = document.getElementById("fileDropText");
const emptyState = document.getElementById("emptyState");
const detail = document.getElementById("detail");
const detailImage = document.getElementById("detailImage");
const detailName = document.getElementById("detailName");
const deleteImageBtn = document.getElementById("deleteImage");
const labelForm = document.getElementById("labelForm");
const labelInput = document.getElementById("labelInput");
const labelList = document.getElementById("labelList");
const extractBtn = document.getElementById("extractLabel");
const extractBtnText = document.getElementById("extractLabelText");

let selectedImageId = null;

function getApiBase() {
  return localStorage.getItem("labeler_api_base") || DEFAULT_API_BASE;
}

function setApiBase(url) {
  localStorage.setItem("labeler_api_base", url.replace(/\/$/, ""));
}

apiBaseInput.value = getApiBase();

document.getElementById("saveApiBase").addEventListener("click", () => {
  setApiBase(apiBaseInput.value.trim() || DEFAULT_API_BASE);
  apiBaseInput.value = getApiBase();
  checkHealth();
  loadImages();
});

fileInput.addEventListener("change", () => {
  fileDropText.textContent = fileInput.files[0]?.name || "Choose an image, or drop it here";
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

async function loadImages() {
  try {
    const res = await fetch(`${getApiBase()}/images`);
    if (!res.ok) throw new Error();
    const images = await res.json();
    renderGallery(images);
  } catch {
    gallery.innerHTML = "<p>Could not load images. Check the API base URL above.</p>";
  }
}

function renderGallery(images) {
  gallery.innerHTML = "";
  for (const img of images) {
    const div = document.createElement("div");
    div.className = "thumb" + (img.id === selectedImageId ? " selected" : "");
    div.innerHTML = `
      <img src="${getApiBase()}/images/${img.id}/file" alt="${escapeHtml(img.original_name)}" />
      <span class="thumb-name">${escapeHtml(img.original_name)}</span>
    `;
    div.addEventListener("click", () => selectImage(img.id));
    gallery.appendChild(div);
  }
}

async function selectImage(id) {
  selectedImageId = id;
  await loadImages();
  emptyState.classList.add("hidden");
  detail.classList.remove("hidden");

  const res = await fetch(`${getApiBase()}/images/${id}`);
  const image = await res.json();

  detailImage.src = `${getApiBase()}/images/${id}/file`;
  detailName.textContent = image.original_name;
  renderLabels(image.labels);
}

function renderLabels(labels) {
  labelList.innerHTML = "";
  for (const label of labels) {
    const li = document.createElement("li");
    li.className = label.source === "auto" ? "auto" : "";
    const badge = label.source === "auto" ? `<span class="label-badge">auto</span>` : "";
    li.innerHTML = `${badge}<span>${escapeHtml(label.text)}</span><button data-id="${label.id}">&times;</button>`;
    li.querySelector("button").addEventListener("click", () => deleteLabel(label.id));
    labelList.appendChild(li);
  }
}

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  await fetch(`${getApiBase()}/images`, { method: "POST", body: formData });
  fileInput.value = "";
  fileDropText.textContent = "Choose an image, or drop it here";
  loadImages();
});

labelForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = labelInput.value.trim();
  if (!text || !selectedImageId) return;
  await fetch(`${getApiBase()}/images/${selectedImageId}/labels`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  labelInput.value = "";
  selectImage(selectedImageId);
});

extractBtn.addEventListener("click", async () => {
  if (!selectedImageId) return;
  extractBtn.disabled = true;
  extractBtn.classList.add("loading");
  extractBtnText.textContent = "Extracting...";
  try {
    const res = await fetch(`${getApiBase()}/images/${selectedImageId}/extract-label`, {
      method: "POST",
    });
    if (!res.ok) throw new Error();
    await selectImage(selectedImageId);
  } catch {
    extractBtnText.textContent = "Extraction failed";
    setTimeout(() => (extractBtnText.textContent = "Extract label"), 2000);
  } finally {
    extractBtn.disabled = false;
    extractBtn.classList.remove("loading");
    if (extractBtnText.textContent === "Extracting...") {
      extractBtnText.textContent = "Extract label";
    }
  }
});

deleteImageBtn.addEventListener("click", async () => {
  if (!selectedImageId) return;
  await fetch(`${getApiBase()}/images/${selectedImageId}`, { method: "DELETE" });
  selectedImageId = null;
  detail.classList.add("hidden");
  emptyState.classList.remove("hidden");
  loadImages();
});

async function deleteLabel(labelId) {
  await fetch(`${getApiBase()}/labels/${labelId}`, { method: "DELETE" });
  selectImage(selectedImageId);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

checkHealth();
loadImages();
