import asyncio
import json
import logging
import mimetypes
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np


LOG = logging.getLogger("telepresence.idle_assistant")
ROBOT_PROFILE_FILE = "robot_profile.json"


@dataclass
class IdleAssistantConfig:
    root: Path
    mic_device: str
    speaker_device: str = "default"
    whisper_cli: Optional[Path] = None
    whisper_model: Optional[Path] = None
    piper_bin: Optional[Path] = None
    piper_voice: Optional[Path] = None
    env_file: Optional[Path] = None
    llm_provider: str = "local"
    ollama_url: str = "http://127.0.0.1:11434/api/chat"
    ollama_model: str = "gemma3:1b"
    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key_env: str = "OPENAI_API_KEY"
    stt_provider: str = "local"
    stt_model: str = "gpt-4o-mini-transcribe"
    stt_api_base: str = "https://api.openai.com/v1"
    stt_api_key_env: str = "OPENAI_API_KEY"
    language: str = "auto"
    silence_rms: int = 450
    min_speech_seconds: float = 0.5
    silence_seconds: float = 0.9
    max_utterance_seconds: float = 8.0
    busy_retry_seconds: float = 2.0


class IdleAssistant:
    sample_rate = 16000
    chunk_ms = 100
    preferred_speaker_devices = (
        "front:CARD=tegrasndt210ref,DEV=0",
        "sysdefault:CARD=tegrasndt210ref",
        "default",
    )

    def __init__(self, config: IdleAssistantConfig, can_run: Callable[[], bool]):
        self.config = config
        self.can_run = can_run
        self.state = "stopped"
        self.last_error = None
        self.last_heard = ""
        self.last_response = ""
        self.response_count = 0
        self._task = None
        self._stop_event = asyncio.Event()
        self._active_processes = set()
        self._robot_profile_prompt = self._load_robot_profile_prompt()

    def _load_robot_profile_prompt(self) -> str:
        profile_path = self.config.root / ROBOT_PROFILE_FILE
        if not profile_path.exists():
            return ""
        try:
            profile = json.loads(profile_path.read_text())
        except Exception as exc:
            LOG.warning("Failed to read robot profile %s: %s", profile_path, exc)
            return ""

        identity = profile.get("identity", {})
        home = profile.get("home", {})
        construction = profile.get("construction", {})
        personality_notes = profile.get("personality_notes", [])
        local_context = profile.get("local_context", [])

        lines = [
            "Robot profile and local context:",
            f"- Name: {identity.get('name', 'Unknown')}",
        ]
        if identity.get("pronunciation_hint"):
            lines.append(f"- Pronunciation: {identity['pronunciation_hint']}")
        if identity.get("role"):
            lines.append(f"- Role: {identity['role']}")

        location = ", ".join(part for part in [home.get("city", ""), home.get("country", "")] if part)
        if location:
            lines.append(f"- Home: {location}")
        if home.get("lives_with"):
            lines.append(f"- Lives with: {home['lives_with']}")

        if construction.get("summary"):
            lines.append(f"- Build: {construction['summary']}")
        else:
            brain = construction.get("brain")
            heart = construction.get("heart")
            if brain or heart:
                lines.append(f"- Build: brain={brain or 'unknown'}, heart={heart or 'unknown'}")

        if personality_notes:
            lines.append("- Personality notes:")
            lines.extend(f"  - {note}" for note in personality_notes if note)
        if local_context:
            lines.append("- Local context:")
            lines.extend(f"  - {note}" for note in local_context if note)
        lines.append(
            "Use this profile when the user asks about the robot, its identity, family, home, or local context. "
            "Do not force these facts into unrelated answers."
        )
        return "\n".join(lines)

    @staticmethod
    def _estimated_speaking_hold(text: str) -> float:
        words = max(1, len(re.findall(r"\w+", text)))
        # Roughly align the listen-resume moment with browser TTS finishing,
        # so the assistant doesn't start recording its own reply.
        seconds = 0.5 + (words / 2.8)
        return max(1.0, min(7.0, seconds))

    def _assistant_env(self) -> dict:
        env = os.environ.copy()
        # When telepresence runs under sudo, these user-session vars can push
        # arecord/aplay into the wrong PulseAudio session and cause "busy" or
        # ownership errors. The idle assistant should use ALSA devices directly.
        env.pop("XDG_RUNTIME_DIR", None)
        env.pop("PULSE_SERVER", None)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        return env

    def status(self) -> dict:
        return {
            "enabled": self._task is not None,
            "state": self.state,
            "last_error": self.last_error,
            "last_heard": self.last_heard,
            "last_response": self.last_response,
            "response_count": self.response_count,
            "mic_device": self.config.mic_device,
            "model": self.config.ollama_model,
        }

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._resolve_paths()
        self._task = asyncio.create_task(self._run(), name="idle-assistant")
        LOG.info("Idle assistant enabled")

    async def stop(self) -> None:
        self._stop_event.set()
        self.pause()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self.state = "stopped"

    def pause(self) -> None:
        for process in list(self._active_processes):
            self._terminate_process(process)

    async def pause_and_wait(self) -> None:
        self.pause()
        if self._active_processes:
            await asyncio.gather(
                *(process.wait() for process in list(self._active_processes)),
                return_exceptions=True,
            )

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self.can_run():
                self.state = "paused"
                await asyncio.sleep(0.25)
                continue
            try:
                self.state = "listening"
                wav_path = await self._record_utterance()
                if wav_path is None:
                    continue
                self.state = "transcribing"
                text = await self._transcribe(wav_path)
                try:
                    wav_path.unlink(missing_ok=True)
                except TypeError:
                    if wav_path.exists():
                        wav_path.unlink()
                if not text or not self.can_run():
                    continue
                self.last_heard = text
                self.state = "thinking"
                response = await self._chat(text)
                if not response or not self.can_run():
                    continue
                self.last_response = response
                self.response_count += 1
                self.state = "speaking"
                await self._speak(response)
                hold = self._estimated_speaking_hold(response)
                deadline = time.monotonic() + hold
                while time.monotonic() < deadline and not self._stop_event.is_set() and self.can_run():
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                LOG.exception("Idle assistant loop failed")
                await asyncio.sleep(1.0)

    async def _record_utterance(self) -> Optional[Path]:
        bytes_per_sample = 2
        samples_per_chunk = int(self.sample_rate * self.chunk_ms / 1000)
        chunk_bytes = samples_per_chunk * bytes_per_sample
        pre_roll = deque(maxlen=4)
        frames = []
        speech_seconds = 0.0
        silence_after_speech = 0.0
        started = False
        start_time = None

        process = await asyncio.create_subprocess_exec(
            "arecord",
            "-q",
            "-D",
            self.config.mic_device,
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
            env=self._assistant_env(),
        )
        self._active_processes.add(process)
        try:
            while not self._stop_event.is_set() and self.can_run():
                data = await process.stdout.readexactly(chunk_bytes)
                samples = np.frombuffer(data, dtype=np.int16)
                rms = int(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                chunk_seconds = self.chunk_ms / 1000.0
                if not started:
                    pre_roll.append(data)
                    if rms >= self.config.silence_rms:
                        started = True
                        start_time = time.monotonic()
                        frames.extend(pre_roll)
                        speech_seconds += chunk_seconds
                    continue

                frames.append(data)
                if rms >= self.config.silence_rms:
                    speech_seconds += chunk_seconds
                    silence_after_speech = 0.0
                else:
                    silence_after_speech += chunk_seconds

                elapsed = time.monotonic() - start_time
                enough_speech = speech_seconds >= self.config.min_speech_seconds
                if enough_speech and silence_after_speech >= self.config.silence_seconds:
                    break
                if elapsed >= self.config.max_utterance_seconds:
                    break
        except asyncio.IncompleteReadError:
            error = ""
            if process.stderr is not None:
                error = (await process.stderr.read()).decode("utf-8", "replace").strip()
            if error:
                if "Device or resource busy" in error:
                    self.state = "mic_busy"
                    self.last_error = error
                    LOG.warning("Idle assistant microphone is busy: %s", error)
                    await asyncio.sleep(self.config.busy_retry_seconds)
                    return None
                raise RuntimeError(error)
        finally:
            self._terminate_process(process)
            self._active_processes.discard(process)

        if speech_seconds < self.config.min_speech_seconds:
            return None

        fd, filename = tempfile.mkstemp(prefix="robot-question-", suffix=".wav")
        os.close(fd)
        path = Path(filename)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(b"".join(frames))
        return path

    async def _transcribe(self, wav_path: Path) -> str:
        if self.config.stt_provider.lower() == "openai":
            return await self._transcribe_openai(wav_path)
        if not self.config.whisper_cli or not self.config.whisper_model:
            raise RuntimeError("Whisper is not configured")
        language = self.config.language
        if language == "auto" and self.config.whisper_model.name.endswith(".en.bin"):
            language = "en"

        env = os.environ.copy()
        lib_dirs = [
            self.config.root / "whisper.cpp" / "build" / "src",
            self.config.root / "whisper.cpp" / "build" / "ggml" / "src",
        ]
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(str(path) for path in lib_dirs if path.exists())
        if existing:
            env["LD_LIBRARY_PATH"] = f"{env['LD_LIBRARY_PATH']}:{existing}"

        process = await asyncio.create_subprocess_exec(
            str(self.config.whisper_cli),
            "-ng",
            "-nt",
            "-m",
            str(self.config.whisper_model),
            "-l",
            language,
            "-t",
            "4",
            "-f",
            str(wav_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "Whisper failed")
        return self._parse_whisper_text(stdout.decode("utf-8", "replace"))

    async def _transcribe_openai(self, wav_path: Path) -> str:
        return await asyncio.get_running_loop().run_in_executor(None, self._post_openai_transcription, wav_path)

    async def _chat(self, text: str) -> str:
        system_prompt = (
            "You are a friendly assistant inside a telepresence robot. "
            "Answer in the same language as the user. Keep answers short, clear, and spoken-friendly."
        )
        if self._robot_profile_prompt:
            system_prompt = f"{system_prompt}\n\n{self._robot_profile_prompt}"
        if self.config.llm_provider.lower() == "openai":
            payload = {
                "model": self.config.ollama_model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {"role": "user", "content": text},
                ],
                "temperature": 0.4,
            }
            return await asyncio.get_running_loop().run_in_executor(None, self._post_openai_chat, payload)

        payload = {
            "model": self.config.ollama_model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": text},
            ],
            "options": {"temperature": 0.4, "num_predict": 90},
        }
        return await asyncio.get_running_loop().run_in_executor(None, self._post_ollama, payload)

    def _post_ollama(self, payload: dict) -> str:
        request = urllib.request.Request(
            self.config.ollama_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data.get("message", {}).get("content", "").strip()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Ollama unavailable: {exc}") from exc

    def _post_openai_chat(self, payload: dict) -> str:
        api_key = os.getenv(self.config.llm_api_key_env, "")
        if not api_key:
            raise RuntimeError(f"Missing API key in env var {self.config.llm_api_key_env}")

        request = urllib.request.Request(
            f"{self.config.llm_api_base.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"OpenAI chat unavailable: {exc}") from exc

    def _post_openai_transcription(self, wav_path: Path) -> str:
        api_key = os.getenv(self.config.stt_api_key_env, "")
        if not api_key:
            raise RuntimeError(f"Missing API key in env var {self.config.stt_api_key_env}")

        with open(wav_path, "rb") as audio_file:
            audio_bytes = audio_file.read()

        boundary = f"----IdleAssistantBoundary{int(time.time() * 1000)}"
        body = bytearray()

        fields = {
            "model": self.config.stt_model,
        }
        if self.config.language and self.config.language != "auto":
            fields["language"] = self.config.language

        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")

        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="file"; filename="{wav_path.name}"\r\n'.encode("utf-8")
        )
        body.extend(
            f"Content-Type: {mimetypes.guess_type(str(wav_path))[0] or 'audio/wav'}\r\n\r\n".encode("utf-8")
        )
        body.extend(audio_bytes)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        request = urllib.request.Request(
            f"{self.config.stt_api_base.rstrip('/')}/audio/transcriptions",
            data=bytes(body),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data.get("text", "").strip()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"OpenAI transcription unavailable: {exc}") from exc

    async def _speak(self, text: str) -> None:
        if not self.config.piper_bin or not self.config.piper_voice:
            raise RuntimeError("Piper is not configured")
        clean = re.sub(r"[^\w\s,.!?:;()-]", "", text).strip()
        if not clean:
            return
        devices = []
        for device in (self.config.speaker_device, *self.preferred_speaker_devices):
            if device and device not in devices:
                devices.append(device)
        errors = []
        for device in devices:
            try:
                await self._speak_with_device(clean, device)
                if device != self.config.speaker_device:
                    LOG.info("Idle assistant speaker fallback succeeded on %s", device)
                return
            except Exception as exc:
                errors.append(f"{device}: {exc}")
                LOG.warning("Idle assistant playback failed on %s: %s", device, exc)
        raise RuntimeError("No working speaker output found. " + " | ".join(errors))

    async def _speak_with_device(self, clean: str, device: str) -> None:
        LOG.info("Idle assistant speaking on ALSA device %s", device)
        piper = await asyncio.create_subprocess_exec(
            str(self.config.piper_bin),
            "--model",
            str(self.config.piper_voice),
            "--output-raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._assistant_env(),
        )
        aplay = await asyncio.create_subprocess_exec(
            "aplay",
            "-q",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-r",
            "22050",
            "-c",
            "1",
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._assistant_env(),
        )
        self._active_processes.update({piper, aplay})
        try:
            piper.stdin.write(clean.encode("utf-8") + b"\n")
            await piper.stdin.drain()
            piper.stdin.close()
            while self.can_run() and not self._stop_event.is_set():
                chunk = await piper.stdout.read(4096)
                if not chunk:
                    break
                aplay.stdin.write(chunk)
                await aplay.stdin.drain()
        finally:
            if not self.can_run() or self._stop_event.is_set():
                for process in (piper, aplay):
                    self._terminate_process(process)
            if aplay.stdin:
                aplay.stdin.close()
            await asyncio.gather(piper.wait(), aplay.wait(), return_exceptions=True)
            self._active_processes.discard(piper)
            self._active_processes.discard(aplay)
        piper_stderr = b""
        aplay_stderr = b""
        if piper.stderr is not None:
            piper_stderr = await piper.stderr.read()
        if aplay.stderr is not None:
            aplay_stderr = await aplay.stderr.read()
        if piper.returncode not in (0, None):
            raise RuntimeError(
                piper_stderr.decode("utf-8", "replace").strip()
                or f"piper exited with code {piper.returncode}"
            )
        if aplay.returncode not in (0, None):
            raise RuntimeError(
                aplay_stderr.decode("utf-8", "replace").strip()
                or f"aplay exited with code {aplay.returncode}"
            )

    def _resolve_paths(self) -> None:
        root = self.config.root
        if self.config.env_file is None:
            self.config.env_file = root / ".env"
        if self.config.env_file.exists():
            for raw_line in self.config.env_file.read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

        if self.config.whisper_cli is None:
            self.config.whisper_cli = root / "whisper.cpp" / "build" / "bin" / "whisper-cli"
        if self.config.whisper_model is None:
            for name in ("ggml-tiny.bin", "ggml-tiny.en.bin", "ggml-base.en.bin"):
                candidate = root / "whisper.cpp" / "models" / name
                if candidate.exists():
                    self.config.whisper_model = candidate
                    break
        if self.config.piper_bin is None:
            self.config.piper_bin = root / "piper" / "piper"
        if self.config.piper_voice is None:
            self.config.piper_voice = root / "piper" / "en_GB-semaine-medium.onnx"

        missing = [
            str(path)
            for path in (self.config.whisper_cli, self.config.whisper_model, self.config.piper_bin, self.config.piper_voice)
            if path is None or not path.exists()
        ]
        if missing:
            raise RuntimeError(f"Idle assistant missing required files: {', '.join(missing)}")

    @staticmethod
    def _terminate_process(process) -> None:
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass

    @staticmethod
    def _parse_whisper_text(stdout: str) -> str:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return ""
        text = lines[-1]
        if "]" in text:
            text = text.split("]", 1)[1].strip()
        return text
