const telepresenceStatus = document.getElementById("telepresenceStatus");
const assistantStatus = document.getElementById("assistantStatus");
const setupAssistantToggle = document.getElementById("setupAssistantToggle");

let assistantEnabled = false;
let assistantAvailable = false;

function renderStatus(target, entries) {
  target.innerHTML = "";
  Object.entries(entries).forEach(([key, value]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = key;
    dd.textContent = String(value);
    target.append(dt, dd);
  });
}

function updateAssistantButton(status) {
  assistantAvailable = Boolean(status.available);
  assistantEnabled = Boolean(status.enabled);
  setupAssistantToggle.disabled = !assistantAvailable;
  if (!assistantAvailable) {
    setupAssistantToggle.textContent = "IA no disponible";
  } else {
    setupAssistantToggle.textContent = assistantEnabled ? "Pausar IA" : "Start IA";
  }
}

async function refreshSetup() {
  const statusResponse = await fetch("/api/status");
  const status = await statusResponse.json();
  renderStatus(telepresenceStatus, {
    operator: status.operator_connected ? "connected" : "offline",
    display: status.display_connected ? "connected" : "offline",
    serial: status.serial.available ? "connected" : "offline",
    front: status.cameras.front,
    down: status.cameras.down,
  });

  const assistantResponse = await fetch("/api/assistant/status");
  const assistant = await assistantResponse.json();
  renderStatus(assistantStatus, {
    available: Boolean(assistant.available),
    enabled: Boolean(assistant.enabled),
    state: assistant.state || "none",
    listening: assistant.enabled && !assistant.operator_connected ? "yes" : "no",
    heard: assistant.last_heard || "none",
    response: assistant.last_response || "none",
    mic: assistant.mic_device || "none",
    model: assistant.model || "none",
    error: assistant.last_error || "none",
  });
  updateAssistantButton(assistant);
}

async function toggleAssistant() {
  setupAssistantToggle.disabled = true;
  const endpoint = assistantEnabled ? "/api/assistant/stop" : "/api/assistant/start";
  await fetch(endpoint, { method: "POST" });
  await refreshSetup();
}

setupAssistantToggle.addEventListener("click", () => {
  toggleAssistant().catch((error) => {
    renderStatus(assistantStatus, { error: error.message });
  });
});

refreshSetup().catch((error) => {
  renderStatus(telepresenceStatus, { error: error.message });
});
setInterval(() => refreshSetup().catch(() => {}), 3000);
