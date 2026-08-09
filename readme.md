# RTSP Replayer

A lightweight web UI for viewing RTSP camera streams. The Python service reads
camera URLs from `setting.json`, reconnects dropped streams automatically, and
converts the current video frame to an MJPEG stream that ordinary browsers can
display.

## Setup

Python 3.10 or newer is recommended.

```powershell
python -m pip install -r requirements.txt
Copy-Item setting.example.json setting.json
```

Edit `setting.json` with the real RTSP URL, then start the server:

```powershell
python app.py
```

Open <http://127.0.0.1:8080>. Stop the process with `Ctrl+C`.

The web page provides camera selection, live connection state, frame rate, and
start/stop controls. RTSP URLs and credentials are never returned by the API.

## Configuration

The full multi-camera format is shown in `setting.example.json`:

```json
{
  "host": "127.0.0.1",
  "port": 8080,
  "jpeg_quality": 82,
  "reconnect_delay_seconds": 2,
  "auto_start": true,
  "cameras": [
    {
      "name": "Front Gate",
      "url": "rtsp://username:password@192.168.1.10:554/stream1"
    }
  ]
}
```

`auto_start` can also be set on an individual camera. Optional
`open_timeout_ms` and `read_timeout_ms` values default to 10000 milliseconds.
For a single camera, this compact legacy format is also accepted:

```json
{
  "rtsp_url": "rtsp://username:password@192.168.1.10:554/stream1"
}
```

Listen on another address or port without changing the file:

```powershell
python app.py --host 0.0.0.0 --port 9000
```

Binding to `0.0.0.0` exposes the page to the local network. The service has no
login layer, so only do this on a trusted network or place it behind an
authenticated reverse proxy.

## HTTP endpoints

- `GET /`: viewer UI
- `GET /api/cameras`: camera state (never includes RTSP URLs)
- `GET /stream/<camera-id>.mjpg`: browser-compatible MJPEG stream
- `POST /api/cameras/<camera-id>/start`: start or reconnect a camera
- `POST /api/cameras/<camera-id>/stop`: stop a camera
- `GET /health`: service and camera health summary

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

To create a standalone Windows executable (including the static web assets),
run `build.cmd`. The result is written to `dist\rtsp-replayer.exe`.
