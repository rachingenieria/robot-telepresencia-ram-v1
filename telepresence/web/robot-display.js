const callerAudio = document.getElementById("callerAudio");
const displayStatus = document.getElementById("displayStatus");
const voiceCanvas = document.getElementById("voiceCanvas");
const avatarMouth = document.getElementById("avatarMouth");
const assistantToggleBtn = document.getElementById("assistantToggleBtn");
const assistantStateText = document.getElementById("assistantStateText");
const assistantListenText = document.getElementById("assistantListenText");
const assistantHeardText = document.getElementById("assistantHeardText");
const assistantResponseWrap = document.getElementById("assistantResponseWrap");
const assistantResponseText = document.getElementById("assistantResponseText");

let pc = null;
let reconnectTimer = null;
let hasOperatorAudio = false;
let hasOperatorVideo = false;
let audioContext = null;
let analyser = null;
let analyserData = null;
let animationFrameId = null;
let monitorGain = null;
let assistantEnabled = false;
let assistantAvailable = false;
let lastAssistantResponseCount = -1;
let activeAssistantUtterance = null;
let refreshTimer = null;
let lastOperatorConnected = null;
let lastAssistantEnabled = null;

function setStatus(text) {
  displayStatus.textContent = text;
}

function scheduleDisplayRefresh(reason, delayMs = 900) {
  if (refreshTimer !== null) return;
  console.info(`Robot display refresh scheduled: ${reason}`);
  setStatus(`Refreshing display: ${reason}...`);
  refreshTimer = window.setTimeout(() => {
    window.location.reload();
  }, delayMs);
}

function setFaceState(state) {
  document.body.classList.remove(
    "face-idle",
    "face-listening",
    "face-thinking",
    "face-speaking",
    "face-paused",
  );
  document.body.classList.add(`face-${state}`);
}

function ensureCallerAudioPlayback() {
  if (!callerAudio.srcObject) return;
  callerAudio.play().catch((error) => {
    console.warn("Display audio playback deferred:", error);
  });
}

function stopAssistantBrowserSpeech() {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.resume();
  activeAssistantUtterance = null;
}

function speakAssistantBrowserText(text, responseCount) {
  if (!("speechSynthesis" in window) || !text) return;
  if (hasOperatorAudio) return;
  if (responseCount === lastAssistantResponseCount) return;
  stopAssistantBrowserSpeech();
  window.speechSynthesis.resume();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  utterance.volume = 1.0;
  utterance.lang = /[áéíóúñ¿¡]/i.test(text) ? "es-ES" : "en-US";
  utterance.onend = () => {
    if (activeAssistantUtterance === utterance) {
      activeAssistantUtterance = null;
    }
  };
  activeAssistantUtterance = utterance;
  lastAssistantResponseCount = responseCount;
  window.speechSynthesis.speak(utterance);
}

function describeAssistantState(status) {
  if (!status.available) return "No disponible";
  if (!status.enabled) return "Lista";
  if (status.operator_connected) return "En espera";
  const labels = {
    listening: "Activa",
    transcribing: "Transcribiendo",
    thinking: "Pensando",
    speaking: "Hablando",
    mic_busy: "Mic ocupado",
    paused: "Pausada",
    stopped: "Pausada",
  };
  return labels[status.state] || status.state || "Activa";
}

function describeAssistantListening(status) {
  if (!status.available || !status.enabled) return "Inactiva";
  if (status.operator_connected) return "Operador conectado";
  if (status.state === "listening") return "Oyendo";
  if (status.state === "transcribing") return "Texto";
  if (status.state === "thinking") return "Respuesta";
  if (status.state === "speaking") return "Voz";
  if (status.state === "mic_busy") return "Mic ocupado";
  return "Activa";
}

function setAssistantButton(status) {
  assistantAvailable = Boolean(status.available);
  assistantEnabled = Boolean(status.enabled);
  assistantToggleBtn.disabled = !assistantAvailable;
  assistantStateText.textContent = describeAssistantState(status);
  assistantListenText.textContent = describeAssistantListening(status);
  assistantHeardText.textContent = status.last_heard ? `Escuche: ${status.last_heard}` : "Sin texto capturado.";
  const responseText = (status.last_response || "").trim();
  const responseCount = Number(status.response_count || 0);
  const isPreparingResponse = status.state === "thinking" || status.state === "speaking";
  assistantResponseWrap.classList.toggle("hidden", !responseText);
  assistantResponseText.textContent = responseText || "Sin respuesta.";
  assistantResponseText.classList.toggle("scrolling", isPreparingResponse && responseText.length > 36);
  avatarMouth.classList.toggle("assistant-speaking", status.state === "speaking" && !hasOperatorAudio);
  if (!assistantEnabled) {
    setFaceState("idle");
  } else if (status.operator_connected) {
    setFaceState("paused");
  } else if (status.state === "listening") {
    setFaceState("listening");
  } else if (status.state === "thinking" || status.state === "transcribing") {
    setFaceState("thinking");
  } else if (status.state === "speaking") {
    setFaceState("speaking");
  } else {
    setFaceState("idle");
  }
  if (status.state === "speaking" && responseText) {
    speakAssistantBrowserText(responseText, responseCount);
  }
  if (lastOperatorConnected === true && status.operator_connected === false) {
    scheduleDisplayRefresh("operator disconnected");
  }
  if (lastAssistantEnabled !== null && lastAssistantEnabled !== assistantEnabled) {
    scheduleDisplayRefresh(assistantEnabled ? "assistant resumed" : "assistant paused");
  }
  lastOperatorConnected = Boolean(status.operator_connected);
  lastAssistantEnabled = assistantEnabled;
  if (!assistantAvailable) {
    assistantToggleBtn.textContent = "IA no disponible";
    assistantToggleBtn.title = "Start server with IDLE_ASSISTANT=1 to enable local AI";
    return;
  }
  assistantToggleBtn.textContent = assistantEnabled ? "Pausar IA" : "Start IA";
  assistantToggleBtn.title = status.operator_connected
    ? "La IA espera mientras el operador esta conectado"
    : (status.state ? `IA: ${status.state}` : "Asistente local");
}

async function fetchAssistantStatus() {
  try {
    const response = await fetch("/api/assistant/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    setAssistantButton(await response.json());
  } catch (error) {
    assistantToggleBtn.disabled = true;
    assistantToggleBtn.textContent = "AI ?";
    assistantToggleBtn.title = error.message;
  }
}

async function toggleAssistant() {
  if (!assistantAvailable) return;
  assistantToggleBtn.disabled = true;
  try {
    const endpoint = assistantEnabled ? "/api/assistant/stop" : "/api/assistant/start";
    const response = await fetch(endpoint, { method: "POST" });
    const payload = await response.json();
    setAssistantButton(payload);
  } catch (error) {
    assistantToggleBtn.title = error.message;
  } finally {
    fetchAssistantStatus();
  }
}

function unlockAudio() {
  if (!audioContext) return;
  if (audioContext.state === "suspended") {
    audioContext.resume().catch(() => {});
  }
  ensureCallerAudioPlayback();
}

window.addEventListener("pointerdown", unlockAudio, { passive: true });
window.addEventListener("touchstart", unlockAudio, { passive: true });
assistantToggleBtn.addEventListener("click", toggleAssistant);

function stopVisualizer() {
  if (animationFrameId !== null) {
    cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
  }
  if (audioContext) {
    audioContext.close().catch(() => {});
  }
  audioContext = null;
  analyser = null;
  analyserData = null;
  monitorGain = null;
  avatarMouth.style.transform = "translateX(-50%) scaleX(1) scaleY(0.85)";
  avatarMouth.classList.remove("assistant-speaking");
  stopAssistantBrowserSpeech();
  callerAudio.pause();
  callerAudio.srcObject = null;
  callerAudio.onloadedmetadata = null;
  callerAudio.oncanplay = null;
}

function startVisualizer(stream) {
  stopVisualizer();
  const track = stream.getAudioTracks()[0];
  if (!track) return;

  const canvasCtx = voiceCanvas.getContext("2d");
  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioContext.createMediaStreamSource(new MediaStream([track]));
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 256;
  analyserData = new Uint8Array(analyser.frequencyBinCount);
  source.connect(analyser);
  monitorGain = audioContext.createGain();
  monitorGain.gain.value = 1.0;
  source.connect(monitorGain);
  monitorGain.connect(audioContext.destination);

  const draw = () => {
    animationFrameId = requestAnimationFrame(draw);
    analyser.getByteFrequencyData(analyserData);
    let energy = 0;
    for (let i = 0; i < analyserData.length; i += 1) energy += analyserData[i];
    const level = energy / (analyserData.length * 255);

    const w = voiceCanvas.width;
    const h = voiceCanvas.height;
    canvasCtx.clearRect(0, 0, w, h);
    canvasCtx.strokeStyle = "rgba(86, 227, 184, 0.9)";
    canvasCtx.lineWidth = 4;
    canvasCtx.beginPath();
    for (let x = 0; x < w; x += 6) {
      const p = x / w;
      const wave = Math.sin((p * 14) + performance.now() * 0.008) * (16 + level * 120);
      const y = h * 0.5 + wave;
      if (x === 0) canvasCtx.moveTo(x, y);
      else canvasCtx.lineTo(x, y);
    }
    canvasCtx.stroke();

    const mouthScaleY = Math.max(0.85, 0.85 + level * 2.8);
    avatarMouth.style.transform = `translateX(-50%) scaleX(1) scaleY(${mouthScaleY.toFixed(3)})`;
  };
  draw();
}

function attachCallerAudioStream(stream) {
  callerAudio.pause();
  callerAudio.srcObject = null;
  callerAudio.srcObject = stream;
  callerAudio.autoplay = true;
  callerAudio.playsInline = true;
  callerAudio.muted = false;
  callerAudio.volume = 1.0;
  callerAudio.onloadedmetadata = () => ensureCallerAudioPlayback();
  callerAudio.oncanplay = () => ensureCallerAudioPlayback();
  ensureCallerAudioPlayback();
}

function refreshStatus() {
  if (hasOperatorAudio) {
    setStatus("Operator voice live");
    return;
  }
  if (pc && (pc.connectionState === "connected" || pc.connectionState === "connecting")) {
    setStatus("Connected, waiting for operator voice...");
    return;
  }
  setStatus("Waiting for operator...");
}

async function connectDisplay() {
  hasOperatorAudio = false;
  hasOperatorVideo = false;
  setStatus("Connecting display...");
  pc = new RTCPeerConnection();

  pc.ontrack = (event) => {
    const [stream] = event.streams;
    if (event.track.kind === "video") {
      hasOperatorVideo = true;
      refreshStatus();
    } else if (event.track.kind === "audio") {
      hasOperatorAudio = true;
      stopAssistantBrowserSpeech();
      const audioStream = stream || new MediaStream([event.track]);
      event.track.onunmute = () => {
        hasOperatorAudio = true;
        ensureCallerAudioPlayback();
        refreshStatus();
      };
      event.track.onmute = () => {
        hasOperatorAudio = false;
        refreshStatus();
      };
      event.track.onended = () => {
        hasOperatorAudio = false;
        callerAudio.pause();
        callerAudio.srcObject = null;
        refreshStatus();
        scheduleDisplayRefresh("operator audio ended", 700);
      };
      attachCallerAudioStream(audioStream);
      startVisualizer(audioStream);
      refreshStatus();
    }
  };

  pc.addTransceiver("audio", { direction: "recvonly" });
  pc.addTransceiver("video", { direction: "recvonly" });
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  const response = await fetch("/offer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sdp: pc.localDescription.sdp,
      type: pc.localDescription.type,
      role: "display",
    }),
  });

  const answer = await response.json();
  await pc.setRemoteDescription(answer);
  refreshStatus();

  pc.onconnectionstatechange = () => {
    refreshStatus();
    if (pc.connectionState === "failed" || pc.connectionState === "closed") {
      scheduleDisplayRefresh(`peer ${pc.connectionState}`, 700);
      scheduleReconnect();
    }
  };
}

async function disconnectDisplay() {
  if (pc) {
    pc.close();
    pc = null;
  }
  stopVisualizer();
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  setStatus("Reconnecting...");
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    await disconnectDisplay();
    try {
      await connectDisplay();
    } catch (error) {
      console.error(error);
      scheduleReconnect();
    }
  }, 3000);
}

connectDisplay().catch((error) => {
  console.error(error);
  setStatus(`Display failed: ${error.message}`);
  scheduleReconnect();
});

setFaceState("idle");
fetchAssistantStatus();
setInterval(fetchAssistantStatus, 3000);
