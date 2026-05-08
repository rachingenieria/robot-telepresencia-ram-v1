const frontVideo = document.getElementById("frontVideo");
const downVideo = document.getElementById("downVideo");
const localVideo = document.getElementById("localVideo");
const robotAudio = document.getElementById("robotAudio");
const localPreviewCard = document.getElementById("localPreviewCard");
const connectBtn = document.getElementById("connectBtn");
const disconnectBtn = document.getElementById("disconnectBtn");
const stopBtn = document.getElementById("stopBtn");
const centerHeadBtn = document.getElementById("centerHeadBtn");
const swapCamerasBtn = document.getElementById("swapCamerasBtn");
const statusText = document.getElementById("statusText");
const urlParams = new URLSearchParams(window.location.search);
const sendOperatorVideo = urlParams.get("video") === "1";
const sendOperatorAudio = urlParams.get("audio") !== "0";

let pc = null;
let controlChannel = null;
let localStream = null;
let headPan = 0;
let headTilt = 0;
let remoteVideoCount = 0;
let autoConnectAttempted = false;

async function attachVideoStream(element, stream) {
  element.srcObject = stream;
  element.autoplay = true;
  element.playsInline = true;
  element.muted = true;
  try {
    await element.play();
  } catch (error) {
    console.warn("Video autoplay deferred:", error);
  }
}

function setStatus(text) {
  statusText.textContent = text;
}

function setConnected(connected) {
  connectBtn.disabled = connected;
  disconnectBtn.disabled = !connected;
  stopBtn.disabled = !connected;
  centerHeadBtn.disabled = !connected;
}

async function connect() {
  if (pc) {
    return;
  }

  const secureMediaAllowed =
    window.isSecureContext ||
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1";

  if (secureMediaAllowed) {
    setStatus(sendOperatorVideo ? "Requesting operator audio/video..." : "Requesting operator audio...");
    try {
      const wantsMedia = sendOperatorAudio || sendOperatorVideo;
      if (wantsMedia) {
        localStream = await navigator.mediaDevices.getUserMedia({
          video: sendOperatorVideo ? { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } } : false,
          audio: sendOperatorAudio,
        });
      }

      const hasLocalVideo = localStream && localStream.getVideoTracks().length > 0;
      if (hasLocalVideo) {
        localVideo.srcObject = localStream;
        localPreviewCard.classList.remove("hidden");
      } else {
        localPreviewCard.classList.add("hidden");
      }
    } catch (error) {
      console.warn("Local media unavailable, continuing in view/control mode.", error);
      setStatus("Connecting without operator media...");
      localPreviewCard.classList.add("hidden");
    }
  } else {
    setStatus("Connecting in view/control mode...");
    localPreviewCard.classList.add("hidden");
  }

  pc = new RTCPeerConnection();
  remoteVideoCount = 0;
  controlChannel = pc.createDataChannel("robot-control");
  controlChannel.onopen = () => setStatus("Connected");
  controlChannel.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "ack") {
        setStatus(`Drive ${payload.state.speed} / Turn ${payload.state.turn}`);
      }
    } catch (_) {}
  };

  pc.ontrack = (event) => {
    if (event.track.kind === "video") {
      const stream = new MediaStream([event.track]);
      if (remoteVideoCount === 0) {
        attachVideoStream(frontVideo, stream);
      } else if (remoteVideoCount === 1) {
        attachVideoStream(downVideo, stream);
      } else if (!downVideo.srcObject) {
        attachVideoStream(downVideo, stream);
      } else {
        attachVideoStream(frontVideo, stream);
      }
      remoteVideoCount += 1;
      setStatus("Robot video connected");
    } else if (event.track.kind === "audio") {
      const audioStream = new MediaStream([event.track]);
      robotAudio.srcObject = audioStream;
      robotAudio.autoplay = true;
      robotAudio.playsInline = true;
      robotAudio.muted = false;
      robotAudio.play().catch((error) => {
        console.warn("Robot audio autoplay deferred:", error);
      });
      setStatus("Robot audio connected");
    }
  };

  pc.onconnectionstatechange = () => {
    setStatus(`Peer ${pc.connectionState}`);
  };

  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });

  if (localStream) {
    localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));
  }

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  const response = await fetch("/offer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sdp: pc.localDescription.sdp,
      type: pc.localDescription.type,
      role: "operator",
    }),
  });
  const answer = await response.json();
  await pc.setRemoteDescription(answer);
  setConnected(true);
  setStatus(sendOperatorAudio && !sendOperatorVideo ? "Peer connected (audio only)" : "Peer connected");
}

async function disconnect() {
  if (controlChannel) {
    controlChannel.close();
    controlChannel = null;
  }
  if (pc) {
    pc.getSenders().forEach((sender) => sender.track && sender.track.stop());
    pc.close();
    pc = null;
  }
  if (localStream) {
    localStream.getTracks().forEach((track) => track.stop());
    localStream = null;
  }
  remoteVideoCount = 0;
  frontVideo.srcObject = null;
  downVideo.srcObject = null;
  localVideo.srcObject = null;
  robotAudio.srcObject = null;
  localPreviewCard.classList.add("hidden");
  setConnected(false);
  setStatus("Disconnected");
}

async function reconnect() {
  await disconnect();
  await new Promise((resolve) => setTimeout(resolve, 350));
  await connect();
}

async function swapCameras() {
  try {
    setStatus("Swapping cameras...");
    const response = await fetch("/api/cameras/swap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    await reconnect();
    setStatus("Cameras swapped");
  } catch (error) {
    console.error(error);
    setStatus(`Swap failed: ${error.message}`);
  }
}

function sendControl(payload) {
  if (!controlChannel || controlChannel.readyState !== "open") {
    return;
  }
  controlChannel.send(JSON.stringify(payload));
}

function attachPad(padId, knobId, onMove, onRelease) {
  const pad = document.getElementById(padId);
  const knob = document.getElementById(knobId);
  const state = { active: false };

  const centerKnob = () => {
    knob.style.left = "50%";
    knob.style.top = "50%";
  };

  const handleMove = (clientX, clientY) => {
    const rect = pad.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const maxRadius = rect.width * 0.35;
    let dx = clientX - cx;
    let dy = clientY - cy;
    const distance = Math.hypot(dx, dy);
    if (distance > maxRadius) {
      const scale = maxRadius / distance;
      dx *= scale;
      dy *= scale;
    }
    const nx = dx / maxRadius;
    const ny = dy / maxRadius;
    knob.style.left = `${50 + nx * 35}%`;
    knob.style.top = `${50 + ny * 35}%`;
    onMove(nx, ny);
  };

  pad.addEventListener("pointerdown", (event) => {
    state.active = true;
    pad.setPointerCapture(event.pointerId);
    handleMove(event.clientX, event.clientY);
  });

  pad.addEventListener("pointermove", (event) => {
    if (!state.active) return;
    handleMove(event.clientX, event.clientY);
  });

  const release = () => {
    if (!state.active) return;
    state.active = false;
    centerKnob();
    onRelease();
  };

  pad.addEventListener("pointerup", release);
  pad.addEventListener("pointercancel", release);
  pad.addEventListener("pointerleave", release);

  centerKnob();
}

attachPad("drivePad", "driveKnob",
  (x, y) => sendControl({ type: "drive", x, y: -y, mode: 1 }),
  () => sendControl({ type: "stop" })
);

attachPad("headPad", "headKnob",
  (x, y) => {
    headPan = Math.round(-x * 90);
    headTilt = Math.round(y * 90);
    sendControl({ type: "servo", pan: headPan, tilt: headTilt });
  },
  () => {}
);

connectBtn.addEventListener("click", async () => {
  try {
    await connect();
  } catch (error) {
    console.error(error);
    setStatus(`Connect failed: ${error.message}`);
    await disconnect();
  }
});

disconnectBtn.addEventListener("click", () => disconnect());
stopBtn.addEventListener("click", () => sendControl({ type: "stop" }));
centerHeadBtn.addEventListener("click", () => {
  headPan = 0;
  headTilt = 0;
  sendControl({ type: "center_head" });
});
swapCamerasBtn.addEventListener("click", async () => swapCameras());

setConnected(false);

window.addEventListener("load", async () => {
  if (autoConnectAttempted) {
    return;
  }
  autoConnectAttempted = true;
  try {
    await connect();
  } catch (error) {
    console.error(error);
    setStatus(`Auto-connect failed: ${error.message}`);
    await disconnect();
  }
});
