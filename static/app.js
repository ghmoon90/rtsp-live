const elements = {
  select: document.querySelector("#camera-select"),
  stream: document.querySelector("#stream"),
  empty: document.querySelector("#empty-state"),
  emptyTitle: document.querySelector("#empty-title"),
  emptyDetail: document.querySelector("#empty-detail"),
  cameraName: document.querySelector("#camera-name"),
  fps: document.querySelector("#fps"),
  start: document.querySelector("#start"),
  stop: document.querySelector("#stop"),
  status: document.querySelector("#overall-status"),
  detail: document.querySelector("#detail"),
  lastFrame: document.querySelector("#last-frame"),
  form: document.querySelector("#camera-form"),
  editorTitle: document.querySelector("#editor-title"),
  nameInput: document.querySelector("#camera-input-name"),
  urlInput: document.querySelector("#camera-input-url"),
  autoStartInput: document.querySelector("#camera-input-auto-start"),
  addCamera: document.querySelector("#add-camera"),
  cancelCamera: document.querySelector("#cancel-camera"),
  saveCamera: document.querySelector("#save-camera"),
  formMessage: document.querySelector("#form-message"),
};

let cameras = [];
let selectedId = null;
let imageCameraId = null;
let editorMode = "edit";
let editorDirty = false;
let saving = false;

function selectedCamera() {
  return cameras.find((camera) => camera.id === selectedId);
}

function stateLabel(state) {
  return {
    live: "Live",
    connecting: "Connecting",
    reconnecting: "Reconnecting",
    stopped: "Stopped",
    error: "Error",
  }[state] || "Waiting";
}

function showPlaceholder(title, detail) {
  elements.emptyTitle.textContent = title;
  elements.emptyDetail.textContent = detail;
  elements.empty.classList.remove("hidden");
  elements.stream.classList.remove("visible");
}

function openImage(cameraId, force = false) {
  if (!force && imageCameraId === cameraId) return;
  imageCameraId = cameraId;
  elements.stream.src = `/stream/${encodeURIComponent(cameraId)}.mjpg?t=${Date.now()}`;
}

function closeImage() {
  imageCameraId = null;
  elements.stream.removeAttribute("src");
  elements.stream.classList.remove("visible");
  elements.empty.classList.remove("hidden");
}

function setFormMessage(message = "", kind = "") {
  elements.formMessage.textContent = message;
  elements.formMessage.className = `form-message ${kind}`.trim();
}

function showCameraSettings(camera, force = false) {
  if (!camera) {
    beginCreate(true);
    return;
  }
  if (!force && (editorMode === "create" || editorDirty || saving)) return;
  editorMode = "edit";
  editorDirty = false;
  elements.editorTitle.textContent = camera.name;
  elements.nameInput.value = camera.name;
  elements.urlInput.value = camera.url;
  elements.autoStartInput.checked = camera.auto_start;
  elements.cancelCamera.classList.add("hidden");
  elements.saveCamera.textContent = "Save changes";
  setFormMessage();
}

function beginCreate(force = false) {
  if (!force && editorDirty && !window.confirm("Discard your unsaved camera changes?")) return;
  editorMode = "create";
  editorDirty = false;
  elements.editorTitle.textContent = "Add a new camera";
  elements.nameInput.value = "";
  elements.urlInput.value = "";
  elements.autoStartInput.checked = true;
  elements.cancelCamera.classList.toggle("hidden", cameras.length === 0);
  elements.saveCamera.textContent = "Add camera";
  setFormMessage();
  elements.nameInput.focus();
}

function render() {
  const camera = selectedCamera();
  const hasCameras = cameras.length > 0;
  elements.select.disabled = !hasCameras;
  elements.start.disabled = !camera || camera.running;
  elements.stop.disabled = !camera || !camera.running;

  if (!camera) {
    elements.cameraName.textContent = "No camera selected";
    elements.fps.textContent = "";
    elements.status.className = "status-pill waiting";
    elements.status.lastElementChild.textContent = "No cameras";
    elements.detail.textContent = "No cameras are configured yet.";
    elements.lastFrame.textContent = "";
    showPlaceholder("No camera configured", "Use Add camera below to create one.");
    return;
  }

  elements.cameraName.textContent = camera.name;
  elements.fps.textContent = camera.state === "live" ? `${camera.fps.toFixed(1)} FPS` : "";
  elements.detail.textContent = camera.detail || stateLabel(camera.state);
  elements.lastFrame.textContent = camera.lastFrameAt
    ? `Last frame ${new Date(camera.lastFrameAt).toLocaleTimeString()}`
    : "No frames received";

  const visualState = camera.state === "live" ? "live" :
    camera.state === "error" ? "error" : "waiting";
  elements.status.className = `status-pill ${visualState}`;
  elements.status.lastElementChild.textContent = stateLabel(camera.state);

  if (camera.running) {
    openImage(camera.id);
  } else {
    closeImage();
  }
  if (camera.state !== "live") {
    showPlaceholder(stateLabel(camera.state), camera.detail || "Waiting for a video frame.");
  }
}

function updateSelect() {
  const previousId = selectedId;
  elements.select.replaceChildren();
  cameras.forEach((camera) => {
    const option = document.createElement("option");
    option.value = camera.id;
    option.textContent = camera.name;
    elements.select.append(option);
  });
  if (!cameras.length) {
    const option = document.createElement("option");
    option.textContent = "No cameras configured";
    elements.select.append(option);
  }
  if (cameras.some((camera) => camera.id === previousId)) {
    selectedId = previousId;
  } else {
    selectedId = cameras[0]?.id || null;
  }
  elements.select.value = selectedId || "";
}

async function refresh() {
  try {
    const response = await fetch("/api/cameras", { cache: "no-store" });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    const payload = await response.json();
    const listChanged = payload.cameras.map((item) => `${item.id}\u0000${item.name}`).join("|") !==
      cameras.map((item) => `${item.id}\u0000${item.name}`).join("|");
    cameras = payload.cameras;
    if (listChanged || selectedId === null) updateSelect();
    if (editorMode === "edit" && !editorDirty && !saving) {
      showCameraSettings(selectedCamera(), true);
    }
    render();
  } catch (error) {
    elements.status.className = "status-pill error";
    elements.status.lastElementChild.textContent = "Offline";
    elements.detail.textContent = error.message;
    elements.start.disabled = true;
    elements.stop.disabled = true;
    showPlaceholder("Server unavailable", "Check that the RTSP Replayer process is running.");
  }
}

async function action(name) {
  if (!selectedId) return;
  elements.start.disabled = true;
  elements.stop.disabled = true;
  if (name === "stop") closeImage();
  try {
    const response = await fetch(`/api/cameras/${encodeURIComponent(selectedId)}/${name}`, {
      method: "POST",
    });
    if (!response.ok) throw new Error(`Request failed with ${response.status}`);
    if (name === "start") openImage(selectedId, true);
  } catch (error) {
    elements.detail.textContent = error.message;
  }
  await refresh();
}

async function saveCamera(event) {
  event.preventDefault();
  if (saving) return;

  const payload = {
    name: elements.nameInput.value.trim(),
    url: elements.urlInput.value.trim(),
    auto_start: elements.autoStartInput.checked,
  };
  if (!payload.name || !payload.url) {
    setFormMessage("Name and RTSP URL are required.", "error");
    return;
  }
  if (!/^rtsps?:\/\//i.test(payload.url)) {
    setFormMessage("URL must begin with rtsp:// or rtsps://.", "error");
    return;
  }

  saving = true;
  elements.saveCamera.disabled = true;
  elements.addCamera.disabled = true;
  setFormMessage(editorMode === "create" ? "Adding camera…" : "Saving changes…");
  const isCreate = editorMode === "create";
  const target = isCreate ? "/api/cameras" : `/api/cameras/${encodeURIComponent(selectedId)}`;
  try {
    const response = await fetch(target, {
      method: isCreate ? "POST" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let result = {};
    try {
      result = await response.json();
    } catch (_error) {
      // The status code below still provides a useful error when a proxy
      // returns a non-JSON response.
    }
    if (!response.ok) throw new Error(result.error || `Request failed with ${response.status}`);
    selectedId = result.id;
    editorMode = "edit";
    editorDirty = false;
    closeImage();
    await refresh();
    showCameraSettings(selectedCamera(), true);
    setFormMessage(isCreate ? "Camera added." : "Changes saved.", "success");
  } catch (error) {
    setFormMessage(error.message, "error");
  } finally {
    saving = false;
    elements.saveCamera.disabled = false;
    elements.addCamera.disabled = false;
  }
}

elements.select.addEventListener("change", () => {
  const nextId = elements.select.value;
  if (editorDirty && !window.confirm("Discard your unsaved camera changes?")) {
    elements.select.value = selectedId || "";
    return;
  }
  selectedId = nextId;
  editorMode = "edit";
  editorDirty = false;
  closeImage();
  showCameraSettings(selectedCamera(), true);
  render();
});
elements.start.addEventListener("click", () => action("start"));
elements.stop.addEventListener("click", () => action("stop"));
elements.addCamera.addEventListener("click", () => beginCreate());
elements.cancelCamera.addEventListener("click", () => showCameraSettings(selectedCamera(), true));
elements.form.addEventListener("submit", saveCamera);
[elements.nameInput, elements.urlInput, elements.autoStartInput].forEach((input) => {
  input.addEventListener("input", () => {
    editorDirty = true;
    if (editorMode === "edit") elements.cancelCamera.classList.remove("hidden");
    setFormMessage();
  });
});
elements.stream.addEventListener("load", () => {
  elements.stream.classList.add("visible");
  elements.empty.classList.add("hidden");
});
elements.stream.addEventListener("error", () => {
  elements.stream.classList.remove("visible");
});

refresh();
window.setInterval(refresh, 2000);
