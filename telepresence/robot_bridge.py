import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import serial


LOG = logging.getLogger("telepresence.robot_bridge")


def clamp(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


@dataclass
class RobotState:
    move_mode: int = 1
    speed: int = 0
    turn: int = 0
    tilt: int = 0
    pan: int = 0
    power: int = 1
    battery_voltage: Optional[float] = None
    battery_cutoff_voltage: float = 7.0
    serial_available: bool = False
    serial_port: Optional[str] = None


@dataclass
class RobotSerialBridge:
    port: str
    baudrate: int = 115200
    timeout: float = 1.0
    _serial: Optional[serial.Serial] = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    state: RobotState = field(default_factory=RobotState, init=False)
    _battery_cache_time: float = field(default=0.0, init=False, repr=False)

    def _ensure_serial(self) -> serial.Serial:
        if self._serial is None:
            if not self.connect():
                raise RuntimeError("Serial bridge is not connected")
        return self._serial

    def connect(self) -> bool:
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
            self.state.serial_available = True
            self.state.serial_port = self.port
            LOG.info("Serial bridge connected on %s", self.port)
            return True
        except Exception as exc:
            self._serial = None
            self.state.serial_available = False
            self.state.serial_port = self.port
            LOG.warning("Serial bridge unavailable on %s: %s", self.port, exc)
            return False

    def close(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    LOG.exception("Failed to close serial bridge")
            self._serial = None
            self.state.serial_available = False

    def _write_line(self, line: str) -> None:
        payload = (line.strip() + "\r\n").encode("utf-8")
        with self._lock:
            self._ensure_serial()
            try:
                self._serial.write(payload)
                self._serial.flush()
            except Exception:
                LOG.exception("Serial write failed; bridge will reconnect on next command")
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                self.state.serial_available = False
                raise
        LOG.debug("Serial -> %s", line.strip())

    def send_move(self, speed: float, turn: float, mode: int = 1) -> None:
        safe_speed = clamp(speed, -100, 100)
        safe_turn = clamp(turn, -100, 100)
        safe_mode = clamp(mode, 0, 9)
        self.state.move_mode = safe_mode
        self.state.speed = safe_speed
        self.state.turn = safe_turn
        self._write_line(f"sm -m {safe_mode} -v {safe_speed} -d {safe_turn}")

    def send_move_from_axes(self, x: float, y: float, mode: int = 1) -> None:
        speed = clamp(y * 100.0, -100, 100)
        turn = clamp(x * 100.0, -100, 100)
        self.send_move(speed=speed, turn=turn, mode=mode)

    def send_servo(self, tilt: float, pan: float) -> None:
        safe_tilt = clamp(tilt, -90, 90)
        safe_pan = clamp(pan, -90, 90)
        self.state.tilt = safe_tilt
        self.state.pan = safe_pan
        self._write_line(f"st -t {safe_tilt} -p {safe_pan}")

    def center_head(self) -> None:
        self.state.tilt = 0
        self.state.pan = 0
        self._write_line("sc")

    def send_power(self, enabled: bool) -> None:
        value = 1 if enabled else 0
        self.state.power = value
        self._write_line(f"pw -p {value}")

    def get_battery_voltage(self, max_age: float = 2.5) -> Optional[float]:
        now = time.monotonic()
        if self.state.battery_voltage is not None and (now - self._battery_cache_time) <= max_age:
            return self.state.battery_voltage

        with self._lock:
            ser = self._ensure_serial()
            try:
                ser.reset_input_buffer()
            except Exception:
                LOG.debug("Could not reset serial input buffer before battery query", exc_info=True)

            ser.write(b"b\r\n")
            ser.flush()
            deadline = time.monotonic() + max(self.timeout, 0.6)
            last_lines = []
            while time.monotonic() < deadline:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                last_lines.append(line)
                if len(last_lines) > 6:
                    last_lines.pop(0)
                if re.fullmatch(r"-?\d+(?:\.\d+)?", line):
                    self.state.battery_voltage = float(line)
                    self._battery_cache_time = time.monotonic()
                    return self.state.battery_voltage

        raise RuntimeError("Battery query returned no voltage value")

    def stop(self) -> None:
        self.send_move(0, 0, mode=self.state.move_mode)
