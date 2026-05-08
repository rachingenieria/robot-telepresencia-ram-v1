import argparse
import asyncio
import json
import logging
import ssl
import time
from fractions import Fraction
from pathlib import Path
from typing import Optional

import av
import cv2
import numpy as np
from aiohttp import web
from aiortc import AudioStreamTrack, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamError

from idle_assistant import IdleAssistant, IdleAssistantConfig
from robot_bridge import RobotSerialBridge


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DEFAULT_CERT_FILE = ROOT / "certs" / "cert.pem"
DEFAULT_KEY_FILE = ROOT / "certs" / "key.pem"
DEFAULT_ASSISTANT_SPEAKER = "front:CARD=tegrasndt210ref,DEV=0"
LOG = logging.getLogger("telepresence.server")


async def run_blocking(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args))


class CameraVideoTrack(VideoStreamTrack):
    def __init__(self, device: str, label: str, width: int = 640, height: int = 480, fps: int = 15):
        super().__init__()
        self.device = device
        self.label = label
        self.width = width
        self.height = height
        self.fps = fps
        self.capture = None
        self._last_open_attempt = 0.0

    def _open_capture(self) -> None:
        now = time.time()
        if self.capture is not None and self.capture.isOpened():
            return
        if now - self._last_open_attempt < 1.0:
            return
        self._last_open_attempt = now
        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            LOG.warning("Failed to open camera %s (%s)", self.label, self.device)
            self.capture = None
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.capture = cap
        LOG.info("Camera %s opened on %s", self.label, self.device)

    def _placeholder_frame(self) -> av.VideoFrame:
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        cv2.putText(img, f"{self.label}: waiting for camera", (20, self.height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        return frame

    async def recv(self) -> av.VideoFrame:
        pts, time_base = await self.next_timestamp()
        self._open_capture()

        frame = None
        if self.capture is not None:
            ok, img = await run_blocking(self.capture.read)
            if ok and img is not None:
                if img.shape[1] != self.width or img.shape[0] != self.height:
                    img = cv2.resize(img, (self.width, self.height))
                frame = av.VideoFrame.from_ndarray(img, format="bgr24")
            else:
                LOG.warning("Read failed on camera %s (%s)", self.label, self.device)
                self.capture.release()
                self.capture = None

        if frame is None:
            frame = self._placeholder_frame()

        frame.pts = pts
        frame.time_base = time_base
        return frame

    def close(self) -> None:
        if self.capture is not None:
            try:
                self.capture.release()
            except Exception:
                LOG.exception("Failed to close capture for %s", self.label)
        self.capture = None


class PlaceholderVideoTrack(VideoStreamTrack):
    def __init__(self, label: str, width: int = 640, height: int = 480):
        super().__init__()
        self.label = label
        self.width = width
        self.height = height

    async def recv(self) -> av.VideoFrame:
        pts, time_base = await self.next_timestamp()
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        cv2.putText(img, self.label, (24, self.height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


class ArecordAudioTrack(AudioStreamTrack):
    sample_rate = 48000
    samples_per_frame = 960

    def __init__(self, device: str):
        super().__init__()
        self.device = device
        self._process = None
        self._pts = 0

    async def _ensure_process(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        LOG.info("Starting robot microphone capture with arecord device: %s", self.device)
        self._process = await asyncio.create_subprocess_exec(
            "arecord",
            "-q",
            "-D",
            self.device,
            "-f",
            "S16_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            "1",
            "-t",
            "raw",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def recv(self) -> av.AudioFrame:
        await self._ensure_process()
        if self.readyState != "live" or self._process.stdout is None:
            raise MediaStreamError

        byte_count = self.samples_per_frame * 2
        try:
            data = await self._process.stdout.readexactly(byte_count)
        except asyncio.IncompleteReadError as exc:
            error = ""
            if self._process.stderr is not None:
                error = (await self._process.stderr.read()).decode("utf-8", "replace").strip()
            LOG.error("Robot microphone capture stopped for %s: %s", self.device, error or exc)
            self.stop()
            raise MediaStreamError

        frame = av.AudioFrame(format="s16", layout="mono", samples=self.samples_per_frame)
        frame.planes[0].update(data)
        frame.sample_rate = self.sample_rate
        frame.pts = self._pts
        frame.time_base = Fraction(1, self.sample_rate)
        self._pts += self.samples_per_frame
        return frame

    def stop(self) -> None:
        super().stop()
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
        self._process = None


class TelepresenceApp:
    def __init__(
        self,
        serial_port: str,
        front_camera: str,
        down_camera: str,
        robot_mic_device: Optional[str] = "default",
        idle_assistant: Optional[IdleAssistant] = None,
    ):
        self.serial = RobotSerialBridge(serial_port)
        self.serial.connect()
        self.front_camera_device = front_camera
        self.down_camera_device = down_camera
        self.front_camera_track = CameraVideoTrack(self.front_camera_device, "Front camera")
        self.down_camera_track = CameraVideoTrack(self.down_camera_device, "Down camera")
        self.relay = MediaRelay()
        self.robot_mic_device = robot_mic_device
        self.robot_mic_track = None
        self.robot_mic_backend: Optional[str] = None
        self.robot_mic_error: Optional[str] = None
        self.idle_assistant = idle_assistant
        self.operator_video = None
        self.operator_audio = None
        self.operator_pc: Optional[RTCPeerConnection] = None
        self.display_pc: Optional[RTCPeerConnection] = None
        self.pcs = set()
        self._cleanup_in_progress = set()

    def reset_robot_tracks(self) -> None:
        LOG.info("Resetting robot camera tracks")
        try:
            self.front_camera_track.close()
        except Exception:
            LOG.exception("Failed to close front camera track during reset")
        try:
            self.down_camera_track.close()
        except Exception:
            LOG.exception("Failed to close down camera track during reset")
        self.front_camera_track = CameraVideoTrack(self.front_camera_device, "Front camera")
        self.down_camera_track = CameraVideoTrack(self.down_camera_device, "Down camera")

    async def swap_cameras(self) -> dict:
        LOG.info("Swapping robot cameras: front=%s down=%s", self.front_camera_device, self.down_camera_device)
        self.front_camera_device, self.down_camera_device = self.down_camera_device, self.front_camera_device

        if self.operator_pc is not None and self.operator_pc.connectionState not in {"closed", "failed"}:
            await self._cleanup_peer(self.operator_pc, "operator")

        self.reset_robot_tracks()
        return self.status()

    async def close(self) -> None:
        if self.idle_assistant is not None:
            await self.idle_assistant.stop()
        self.front_camera_track.close()
        self.down_camera_track.close()
        if self.robot_mic_track is not None:
            try:
                self.robot_mic_track.stop()
            except Exception:
                LOG.exception("Failed to stop robot mic track")
            self.robot_mic_track = None
        await asyncio.gather(*(self._cleanup_peer(pc, "unknown") for pc in list(self.pcs)), return_exceptions=True)
        self.pcs.clear()
        self.serial.close()

    def robot_state_snapshot(self) -> dict:
        return {
            "mode": self.serial.state.move_mode,
            "speed": self.serial.state.speed,
            "turn": self.serial.state.turn,
            "pan": self.serial.state.pan,
            "tilt": self.serial.state.tilt,
            "power": self.serial.state.power,
        }

    def status(self) -> dict:
        return {
            "operator_connected": self.operator_pc is not None and self.operator_pc.connectionState not in {"closed", "failed"},
            "display_connected": self.display_pc is not None and self.display_pc.connectionState not in {"closed", "failed"},
            "serial": {
                "available": self.serial.state.serial_available,
                "port": self.serial.state.serial_port,
            },
            "cameras": {
                "front": self.front_camera_track.device,
                "down": self.down_camera_track.device,
            },
            "robot_state": self.robot_state_snapshot(),
            "robot_mic": {
                "configured_device": self.robot_mic_device,
                "active": self.robot_mic_track is not None,
                "backend": self.robot_mic_backend,
                "error": self.robot_mic_error,
            },
            "idle_assistant": self.idle_assistant.status() if self.idle_assistant is not None else {"enabled": False},
        }

    async def handle_offer(self, request: web.Request) -> web.Response:
        params = await request.json()
        role = params.get("role", "operator")
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        if role == "operator" and self.operator_pc is not None and self.operator_pc.connectionState not in {"closed", "failed"}:
            LOG.info("Closing previous operator peer before accepting a new one")
            await self._cleanup_peer(self.operator_pc, "operator")
        if role == "display" and self.display_pc is not None and self.display_pc.connectionState not in {"closed", "failed"}:
            LOG.info("Closing previous display peer before accepting a new one")
            await self._cleanup_peer(self.display_pc, "display")

        pc = RTCPeerConnection()
        self.pcs.add(pc)
        pc_id = f"{role}-{id(pc)}"
        LOG.info("Peer created: %s", pc_id)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            LOG.info("Peer %s state -> %s", pc_id, pc.connectionState)
            if pc.connectionState in {"failed", "closed"}:
                await self._cleanup_peer(pc, role)

        if role == "operator":
            if self.idle_assistant is not None:
                await self.idle_assistant.pause_and_wait()
            self.reset_robot_tracks()
            self.operator_pc = pc
            pc.addTrack(self.front_camera_track)
            pc.addTrack(self.down_camera_track)
            robot_mic = self._get_robot_mic_track()
            if robot_mic is not None:
                pc.addTrack(robot_mic)

            @pc.on("datachannel")
            def on_datachannel(channel):
                LOG.info("Operator data channel opened: %s", channel.label)

                @channel.on("message")
                def on_message(message):
                    if isinstance(message, str):
                        self._handle_control_message(message, channel)

            @pc.on("track")
            def on_track(track):
                LOG.info("Operator published %s track", track.kind)
                if track.kind == "video":
                    self.operator_video = self.relay.subscribe(track)
                elif track.kind == "audio":
                    self.operator_audio = self.relay.subscribe(track, buffered=False)

                @track.on("ended")
                async def on_ended():
                    LOG.info("Operator %s track ended", track.kind)
                    if track.kind == "video":
                        self.operator_video = None
                    elif track.kind == "audio":
                        self.operator_audio = None

        elif role == "display":
            self.display_pc = pc
            if self.operator_video is not None:
                pc.addTrack(self.operator_video)
            else:
                pc.addTrack(PlaceholderVideoTrack("Waiting for caller"))
            if self.operator_audio is not None:
                pc.addTrack(self.operator_audio)
        else:
            return web.json_response({"error": f"Unknown role: {role}"}, status=400)

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return web.json_response(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
            }
        )

    async def _cleanup_peer(self, pc: RTCPeerConnection, role: str) -> None:
        if pc in self._cleanup_in_progress:
            return
        self._cleanup_in_progress.add(pc)
        if pc in self.pcs:
            self.pcs.remove(pc)
        try:
            await pc.close()
        except Exception:
            LOG.exception("Failed to close peer")
        finally:
            self._cleanup_in_progress.discard(pc)
        if role in {"operator", "unknown"} and self.operator_pc is pc:
            self.operator_pc = None
            self.operator_video = None
            self.operator_audio = None
            if self.robot_mic_track is not None:
                try:
                    self.robot_mic_track.stop()
                except Exception:
                    LOG.exception("Failed to stop robot mic track during operator cleanup")
                self.robot_mic_track = None
                self.robot_mic_backend = None
            self.reset_robot_tracks()
        if role in {"display", "unknown"} and self.display_pc is pc:
            self.display_pc = None

    def _handle_control_message(self, raw_message: str, channel) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            LOG.warning("Invalid control payload: %s", raw_message)
            return

        msg_type = payload.get("type")
        try:
            if msg_type == "drive":
                mode = int(payload.get("mode", 1))
                x = float(payload.get("x", 0.0))
                y = float(payload.get("y", 0.0))
                self.serial.send_move_from_axes(x=x, y=y, mode=mode)
            elif msg_type == "servo":
                pan = float(payload.get("pan", 0.0))
                tilt = float(payload.get("tilt", 0.0))
                self.serial.send_servo(tilt=tilt, pan=pan)
            elif msg_type == "center_head":
                self.serial.center_head()
            elif msg_type == "stop":
                self.serial.stop()
            elif msg_type == "power":
                self.serial.send_power(bool(payload.get("enabled", True)))
            elif msg_type == "ping":
                channel.send(json.dumps({"type": "pong", "ts": payload.get("ts")}))
                return
        except Exception as exc:
            LOG.exception("Control command failed")
            channel.send(json.dumps({"type": "error", "message": str(exc)}))
            return

        channel.send(json.dumps({"type": "ack", "state": self.robot_state_snapshot()}))

    def _get_robot_mic_track(self):
        if self.robot_mic_track is not None and self.robot_mic_track.readyState == "live":
            return self.robot_mic_track
        if not self.robot_mic_device:
            return None
        self.robot_mic_track = None
        self.robot_mic_error = None
        device = self.robot_mic_device
        if device == "default":
            device = "plughw:CARD=camera,DEV=0"
        self.robot_mic_track = ArecordAudioTrack(device)
        self.robot_mic_backend = "arecord"
        LOG.info("Robot microphone track prepared from arecord device: %s", device)
        return self.robot_mic_track

    def assistant_status(self) -> dict:
        if self.idle_assistant is None:
            return {"available": False, "enabled": False}
        status = self.idle_assistant.status()
        status["available"] = True
        status["operator_connected"] = self.operator_pc is not None and self.operator_pc.connectionState not in {"closed", "failed"}
        return status

    async def start_assistant(self) -> dict:
        if self.idle_assistant is None:
            return {"available": False, "enabled": False, "error": "Idle assistant was not configured at startup"}
        await self.idle_assistant.start()
        return self.assistant_status()

    async def stop_assistant(self) -> dict:
        if self.idle_assistant is None:
            return {"available": False, "enabled": False, "error": "Idle assistant was not configured at startup"}
        await self.idle_assistant.stop()
        return self.assistant_status()

async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "index.html")


async def robot_display(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "robot-display.html")


async def setup_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "setup.html")


async def status(request: web.Request) -> web.Response:
    app_state: TelepresenceApp = request.app["telepresence"]
    return web.json_response(app_state.status())


async def swap_cameras(request: web.Request) -> web.Response:
    app_state: TelepresenceApp = request.app["telepresence"]
    return web.json_response(await app_state.swap_cameras())


async def assistant_status(request: web.Request) -> web.Response:
    app_state: TelepresenceApp = request.app["telepresence"]
    return web.json_response(app_state.assistant_status())


async def assistant_start(request: web.Request) -> web.Response:
    app_state: TelepresenceApp = request.app["telepresence"]
    status_payload = await app_state.start_assistant()
    status = 400 if status_payload.get("available") is False else 200
    return web.json_response(status_payload, status=status)


async def assistant_stop(request: web.Request) -> web.Response:
    app_state: TelepresenceApp = request.app["telepresence"]
    status_payload = await app_state.stop_assistant()
    status = 400 if status_payload.get("available") is False else 200
    return web.json_response(status_payload, status=status)


def create_app(args) -> web.Application:
    app = web.Application()
    idle_assistant = None
    if args.idle_assistant:
        assistant_root = Path(args.assistant_root).expanduser().resolve()
        assistant_config_path = assistant_root / "config.jetson.json"
        assistant_settings = {}
        if assistant_config_path.exists():
            try:
                assistant_settings = json.loads(assistant_config_path.read_text())
            except Exception as exc:
                LOG.warning("Failed to read assistant config %s: %s", assistant_config_path, exc)
        assistant_mic_device = args.assistant_mic_device
        if assistant_mic_device == "default":
            assistant_mic_device = args.robot_mic_device
        if assistant_mic_device == "default":
            assistant_mic_device = "plughw:CARD=camera,DEV=0"
        idle_assistant = IdleAssistant(
            IdleAssistantConfig(
                root=assistant_root,
                mic_device=assistant_mic_device,
                speaker_device=args.assistant_speaker_device,
                env_file=assistant_root / ".env",
                llm_provider=assistant_settings.get("llm_provider", "local"),
                ollama_model=assistant_settings.get("llm_model", args.assistant_model),
                llm_api_base=assistant_settings.get("llm_api_base", "https://api.openai.com/v1"),
                llm_api_key_env=assistant_settings.get("llm_api_key_env", "OPENAI_API_KEY"),
                stt_provider=assistant_settings.get("stt_provider", "local"),
                stt_model=assistant_settings.get("stt_model", "gpt-4o-mini-transcribe"),
                stt_api_base=assistant_settings.get("stt_api_base", "https://api.openai.com/v1"),
                stt_api_key_env=assistant_settings.get("stt_api_key_env", "OPENAI_API_KEY"),
                language=assistant_settings.get("transcription_language", args.assistant_language),
            ),
            can_run=lambda: not (
                app["telepresence"].operator_pc is not None
                and app["telepresence"].operator_pc.connectionState not in {"closed", "failed"}
            ),
        )
    app["telepresence"] = TelepresenceApp(
        serial_port=args.serial_port,
        front_camera=args.front_camera,
        down_camera=args.down_camera,
        robot_mic_device=args.robot_mic_device,
        idle_assistant=idle_assistant,
    )
    app.router.add_get("/", index)
    app.router.add_get("/robot-display", robot_display)
    app.router.add_get("/setup", setup_page)
    app.router.add_post("/offer", app["telepresence"].handle_offer)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/cameras/swap", swap_cameras)
    app.router.add_get("/api/assistant/status", assistant_status)
    app.router.add_post("/api/assistant/start", assistant_start)
    app.router.add_post("/api/assistant/stop", assistant_stop)
    app.router.add_static("/static/", WEB_ROOT)

    async def on_startup(app: web.Application):
        loop = asyncio.get_running_loop()

        def _asyncio_exception_filter(_loop, context):
            exc = context.get("exception")
            message = context.get("message", "")
            if isinstance(exc, AttributeError):
                text = str(exc)
                if "'NoneType' object has no attribute 'sendto'" in text or "'NoneType' object has no attribute 'call_exception_handler'" in text:
                    LOG.debug("Ignoring known asyncio/aioice transport teardown race: %s", text)
                    return
            if "Transaction.__retry()" in message:
                LOG.debug("Ignoring STUN retry callback during shutdown: %s", message)
                return
            loop.default_exception_handler(context)

        loop.set_exception_handler(_asyncio_exception_filter)
        if args.idle_assistant_autostart and app["telepresence"].idle_assistant is not None:
            await app["telepresence"].idle_assistant.start()

    async def on_shutdown(app: web.Application):
        await app["telepresence"].close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


def build_ssl_context(args) -> Optional[ssl.SSLContext]:
    if not args.https:
        return None

    cert_file = Path(args.cert_file).expanduser()
    key_file = Path(args.key_file).expanduser()

    if not cert_file.exists():
        raise FileNotFoundError(f"HTTPS certificate not found: {cert_file}")
    if not key_file.exists():
        raise FileNotFoundError(f"HTTPS private key not found: {key_file}")

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(str(cert_file), str(key_file))
    LOG.info("HTTPS enabled with certificate %s", cert_file)
    return ssl_context


def parse_args():
    parser = argparse.ArgumentParser(description="Telepresence server for the Jetson Nano robot")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8088, type=int)
    parser.add_argument("--serial-port", default="/dev/ttyTHS1")
    parser.add_argument("--front-camera", default="/dev/video1")
    parser.add_argument("--down-camera", default="/dev/video0")
    parser.add_argument("--robot-mic-device", default="default", help="ALSA capture device for robot->operator audio (set empty to disable)")
    parser.add_argument("--idle-assistant", action="store_true", help="Enable local question-answer mode when no operator is connected")
    parser.add_argument("--idle-assistant-autostart", action="store_true", help="Start the idle assistant immediately instead of waiting for the display/setup button")
    parser.add_argument("--assistant-root", default=str((ROOT.parent / "be-more-agent").resolve()), help="Path to the local assistant assets (whisper.cpp, piper, models)")
    parser.add_argument("--assistant-mic-device", default="default", help="ALSA capture device for idle assistant (default reuses robot mic)")
    parser.add_argument("--assistant-speaker-device", default=DEFAULT_ASSISTANT_SPEAKER, help="ALSA playback device for idle assistant TTS")
    parser.add_argument("--assistant-model", default="gemma3:1b", help="Ollama model used by the idle assistant")
    parser.add_argument("--assistant-language", default="auto", help="Whisper transcription language")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--https", action="store_true", help="Enable HTTPS using the configured certificate and private key")
    parser.add_argument("--cert-file", default=str(DEFAULT_CERT_FILE), help="Path to the TLS certificate PEM file")
    parser.add_argument("--key-file", default=str(DEFAULT_KEY_FILE), help="Path to the TLS private key PEM file")
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    ssl_context = build_ssl_context(args)
    scheme = "https" if ssl_context else "http"
    LOG.info("Starting telepresence server on %s://%s:%s", scheme, args.host, args.port)
    web.run_app(create_app(args), host=args.host, port=args.port, ssl_context=ssl_context)


if __name__ == "__main__":
    main()
