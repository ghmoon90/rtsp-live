"""Small, dependency-light web viewer for one or more RTSP cameras."""

from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit


LOGGER = logging.getLogger("rtsp-replayer")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


class SettingsError(ValueError):
    """Raised when setting.json does not describe a usable server."""


@dataclass(frozen=True)
class CameraConfig:
    id: str
    name: str
    url: str
    auto_start: bool


@dataclass(frozen=True)
class AppSettings:
    host: str
    port: int
    jpeg_quality: int
    reconnect_delay_seconds: float
    open_timeout_ms: int
    read_timeout_ms: int
    cameras: tuple[CameraConfig, ...]


def _slug(value: str, fallback: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    result = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    return result or fallback


def _bounded_number(
    data: dict[str, Any], key: str, default: int | float, minimum: float, maximum: float
) -> int | float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"'{key}' must be a number")
    if not minimum <= value <= maximum:
        raise SettingsError(f"'{key}' must be between {minimum:g} and {maximum:g}")
    return value


def _bounded_integer(
    data: dict[str, Any], key: str, default: int, minimum: int, maximum: int
) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"'{key}' must be an integer")
    if not minimum <= value <= maximum:
        raise SettingsError(f"'{key}' must be between {minimum} and {maximum}")
    return value


def _validate_url(value: Any, camera_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"Camera '{camera_name}' needs a non-empty 'url'")
    value = value.strip()
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise SettingsError(f"Camera '{camera_name}' has a malformed RTSP URL") from exc
    if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not hostname:
        raise SettingsError(f"Camera '{camera_name}' must have a valid rtsp:// or rtsps:// URL")
    return value


def load_settings(path: str | Path) -> AppSettings:
    """Load and validate either the multi-camera or legacy single-URL format."""
    settings_path = Path(path)
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SettingsError(
            f"Settings file not found: {settings_path}. Copy setting.example.json to setting.json."
        ) from exc
    except json.JSONDecodeError as exc:
        raise SettingsError(f"Invalid JSON in {settings_path}: {exc.msg} (line {exc.lineno})") from exc
    except OSError as exc:
        raise SettingsError(f"Could not read {settings_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise SettingsError("The settings file must contain a JSON object")

    camera_rows = raw.get("cameras")
    if camera_rows is None:
        legacy_url = raw.get("rtsp_url", raw.get("url"))
        camera_rows = [{"name": raw.get("name", "Camera 1"), "url": legacy_url}]
    if not isinstance(camera_rows, list) or not camera_rows:
        raise SettingsError("'cameras' must be a non-empty array")

    default_auto_start = raw.get("auto_start", True)
    if not isinstance(default_auto_start, bool):
        raise SettingsError("'auto_start' must be true or false")

    cameras: list[CameraConfig] = []
    used_ids: set[str] = set()
    for index, row in enumerate(camera_rows, start=1):
        if not isinstance(row, dict):
            raise SettingsError(f"Camera {index} must be a JSON object")
        name = row.get("name", f"Camera {index}")
        if not isinstance(name, str) or not name.strip():
            raise SettingsError(f"Camera {index} needs a non-empty 'name'")
        name = name.strip()
        url = _validate_url(row.get("url", row.get("rtsp_url")), name)
        auto_start = row.get("auto_start", default_auto_start)
        if not isinstance(auto_start, bool):
            raise SettingsError(f"Camera '{name}' has a non-boolean 'auto_start'")

        requested_id = row.get("id")
        if requested_id is not None and (not isinstance(requested_id, str) or not requested_id.strip()):
            raise SettingsError(f"Camera '{name}' has an invalid 'id'")
        base_id = _slug(requested_id or name, f"camera-{index}")
        camera_id = base_id
        suffix = 2
        while camera_id in used_ids:
            camera_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(camera_id)
        cameras.append(CameraConfig(camera_id, name, url, auto_start))

    host = raw.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host.strip():
        raise SettingsError("'host' must be a non-empty string")

    return AppSettings(
        host=host.strip(),
        port=_bounded_integer(raw, "port", 8080, 1, 65535),
        jpeg_quality=_bounded_integer(raw, "jpeg_quality", 82, 30, 100),
        reconnect_delay_seconds=float(
            _bounded_number(raw, "reconnect_delay_seconds", 2.0, 0.1, 60)
        ),
        open_timeout_ms=_bounded_integer(raw, "open_timeout_ms", 10_000, 500, 120_000),
        read_timeout_ms=_bounded_integer(raw, "read_timeout_ms", 10_000, 500, 120_000),
        cameras=tuple(cameras),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CameraStream:
    """Owns one OpenCV capture and publishes its newest encoded frame."""

    def __init__(
        self,
        config: CameraConfig,
        settings: AppSettings,
        cv2_loader: Callable[[], Any] | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self._cv2_loader = cv2_loader or self._import_cv2
        self._condition = threading.Condition()
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._frame_number = 0
        self._state = "stopped"
        self._detail = "Not started"
        self._last_frame_at: str | None = None
        self._fps = 0.0
        self._clients = 0

    @staticmethod
    def _import_cv2() -> Any:
        try:
            import cv2  # type: ignore
        except ImportError as exc:
            raise RuntimeError("OpenCV is not installed; run: pip install -r requirements.txt") from exc
        return cv2

    def _set_state(self, state: str, detail: str) -> None:
        with self._condition:
            self._state = state
            self._detail = detail
            self._condition.notify_all()

    def start(self) -> bool:
        """Start capture if it is not already running. Returns True on a new start."""
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event = threading.Event()
            with self._condition:
                # Do not let a newly connected browser receive a stale image from
                # a previous run while the camera is still reconnecting.
                self._frame = None
            self._set_state("connecting", "Opening stream")
            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(self._stop_event,),
                name=f"camera-{self.config.id}",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> bool:
        """Request capture shutdown. Returns True when a running worker was signalled."""
        with self._lifecycle_lock:
            thread = self._thread
            was_running = bool(thread and thread.is_alive())
            self._stop_event.set()
        with self._condition:
            self._state = "stopped"
            self._detail = "Stopped by user"
            self._condition.notify_all()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        return was_running

    def _safe_error(self, exc: BaseException) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        message = message.replace(self.config.url, "[RTSP URL]")
        parsed = urlsplit(self.config.url)
        if parsed.password:
            message = message.replace(parsed.password, "***")
        return message[:240]

    def _new_capture(self, cv2: Any) -> Any:
        capture = cv2.VideoCapture()
        open_parameters: list[int] = []
        for constant_name, value in (
            ("CAP_PROP_OPEN_TIMEOUT_MSEC", self.settings.open_timeout_ms),
            ("CAP_PROP_READ_TIMEOUT_MSEC", self.settings.read_timeout_ms),
        ):
            prop = getattr(cv2, constant_name, None)
            if prop is not None:
                open_parameters.extend((int(prop), int(value)))
        backend = getattr(cv2, "CAP_FFMPEG", None)
        if backend is not None:
            try:
                opened = capture.open(self.config.url, backend, open_parameters)
            except TypeError:
                # Retain compatibility with older OpenCV builds whose Python
                # binding does not expose the open-parameter overload.
                for index in range(0, len(open_parameters), 2):
                    capture.set(open_parameters[index], open_parameters[index + 1])
                opened = capture.open(self.config.url, backend)
        else:
            opened = capture.open(self.config.url)
        if not opened or not capture.isOpened():
            capture.release()
            raise RuntimeError("Unable to connect to the RTSP stream")
        return capture

    def _capture_loop(self, stop_event: threading.Event) -> None:
        capture = None
        first_attempt = True
        try:
            cv2 = self._cv2_loader()
            encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.settings.jpeg_quality]
            previous_frame_time: float | None = None
            while not stop_event.is_set():
                self._set_state(
                    "connecting" if first_attempt else "reconnecting",
                    "Opening stream" if first_attempt else "Waiting to reconnect",
                )
                first_attempt = False
                try:
                    capture = self._new_capture(cv2)
                    self._set_state("connecting", "Waiting for the first frame")
                    while not stop_event.is_set():
                        ok, frame = capture.read()
                        if not ok or frame is None:
                            raise RuntimeError("The RTSP stream stopped returning frames")
                        encoded_ok, encoded = cv2.imencode(".jpg", frame, encode_params)
                        if not encoded_ok:
                            continue
                        now = time.monotonic()
                        instant_fps = 0.0 if previous_frame_time is None else 1.0 / max(now - previous_frame_time, 0.001)
                        previous_frame_time = now
                        with self._condition:
                            self._frame = encoded.tobytes()
                            self._frame_number += 1
                            self._last_frame_at = _utc_now()
                            self._fps = instant_fps if not self._fps else (self._fps * 0.85 + instant_fps * 0.15)
                            self._state = "live"
                            self._detail = "Streaming"
                            self._condition.notify_all()
                except Exception as exc:  # OpenCV exposes several backend-specific exceptions.
                    if not stop_event.is_set():
                        detail = self._safe_error(exc)
                        LOGGER.warning("Camera %s: %s", self.config.name, detail)
                        self._set_state("reconnecting", detail)
                finally:
                    if capture is not None:
                        capture.release()
                        capture = None
                stop_event.wait(self.settings.reconnect_delay_seconds)
        except Exception as exc:
            if not stop_event.is_set():
                detail = self._safe_error(exc)
                LOGGER.error("Camera %s cannot start: %s", self.config.name, detail)
                self._set_state("error", detail)
        finally:
            if capture is not None:
                capture.release()
            if stop_event.is_set():
                self._set_state("stopped", "Stopped")

    def next_frame(self, after: int, timeout: float = 2.0) -> tuple[bytes | None, int, bool]:
        """Wait for a newer frame; the last item indicates a terminal state."""
        with self._condition:
            self._condition.wait_for(
                lambda: self._frame_number > after or self._state in {"stopped", "error"},
                timeout=timeout,
            )
            terminal = self._state in {"stopped", "error"} and self._frame_number <= after
            if self._frame_number > after:
                return self._frame, self._frame_number, False
            return None, after, terminal

    def add_client(self) -> None:
        with self._condition:
            self._clients += 1

    def remove_client(self) -> None:
        with self._condition:
            self._clients = max(0, self._clients - 1)

    def status(self) -> dict[str, Any]:
        with self._condition:
            thread = self._thread
            return {
                "id": self.config.id,
                "name": self.config.name,
                "state": self._state,
                "detail": self._detail,
                "running": bool(thread and thread.is_alive() and not self._stop_event.is_set()),
                "lastFrameAt": self._last_frame_at,
                "fps": round(self._fps, 1),
                "viewers": self._clients,
            }


class StreamManager:
    def __init__(self, settings: AppSettings, cv2_loader: Callable[[], Any] | None = None) -> None:
        self.cameras = {
            config.id: CameraStream(config, settings, cv2_loader) for config in settings.cameras
        }

    def start_configured(self) -> None:
        for camera in self.cameras.values():
            if camera.config.auto_start:
                camera.start()

    def statuses(self) -> list[dict[str, Any]]:
        return [camera.status() for camera in self.cameras.values()]

    def close(self) -> None:
        for camera in self.cameras.values():
            camera.stop()


class ReplayRequestHandler(BaseHTTPRequestHandler):
    manager: StreamManager
    static_dir = STATIC_DIR
    server_version = "RTSPReplayer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def _send_headers(self, status: HTTPStatus, content_type: str, length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _asset(self, name: str, content_type: str) -> None:
        try:
            body = (self.static_dir / name).read_bytes()
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Asset not found"})
            return
        self._send_headers(HTTPStatus.OK, content_type, len(body))
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            self._asset("index.html", "text/html; charset=utf-8")
        elif path == "/app.css":
            self._asset("app.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            self._asset("app.js", "text/javascript; charset=utf-8")
        elif path == "/api/cameras":
            self._json(HTTPStatus.OK, {"cameras": self.manager.statuses()})
        elif path == "/health":
            statuses = self.manager.statuses()
            healthy = any(item["state"] == "live" for item in statuses)
            self._json(HTTPStatus.OK, {"status": "ok" if healthy else "waiting", "cameras": statuses})
        else:
            match = re.fullmatch(r"/stream/([a-z0-9-]+)\.mjpg", path)
            if match:
                self._stream(match.group(1))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlsplit(self.path).path)
        match = re.fullmatch(r"/api/cameras/([a-z0-9-]+)/(start|stop)", path)
        if not match:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        camera = self.manager.cameras.get(match.group(1))
        if camera is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown camera"})
            return
        if match.group(2) == "start":
            camera.start()
        else:
            camera.stop()
        self._json(HTTPStatus.OK, camera.status())

    def _stream(self, camera_id: str) -> None:
        camera = self.manager.cameras.get(camera_id)
        if camera is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown camera"})
            return
        camera.start()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        sequence = 0
        camera.add_client()
        try:
            while True:
                frame, sequence, terminal = camera.next_frame(sequence)
                if terminal:
                    break
                if frame is None:
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            camera.remove_client()


def create_server(settings: AppSettings, host: str | None = None, port: int | None = None) -> tuple[ThreadingHTTPServer, StreamManager]:
    manager = StreamManager(settings)

    class BoundHandler(ReplayRequestHandler):
        pass

    BoundHandler.manager = manager
    server = ThreadingHTTPServer((host or settings.host, settings.port if port is None else port), BoundHandler)
    server.daemon_threads = True
    return server, manager


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="View RTSP cameras in a web browser")
    parser.add_argument("--settings", default="setting.json", help="settings JSON file")
    parser.add_argument("--host", help="override the configured listen address")
    parser.add_argument("--port", type=int, help="override the configured port")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        settings = load_settings(args.settings)
        if args.port is not None and not 1 <= args.port <= 65535:
            raise SettingsError("--port must be between 1 and 65535")
        server, manager = create_server(settings, args.host, args.port)
    except (SettingsError, OSError) as exc:
        LOGGER.error("%s", exc)
        return 2

    manager.start_configured()
    listen_host, listen_port = server.server_address[:2]
    display_host = "localhost" if listen_host in {"0.0.0.0", "::"} else listen_host
    LOGGER.info("RTSP Replayer is available at http://%s:%s", display_host, listen_port)

    def request_shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, request_shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_shutdown)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        manager.close()
        LOGGER.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
