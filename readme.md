# RTSP Replayer

A lightweight web UI for viewing RTSP camera streams. The Python service reads
camera URLs from `setting.json`, reconnects dropped streams automatically, and
converts the current video frame to an MJPEG stream that ordinary browsers can
display.

## Setup and run

Python 3.10 or newer is recommended.

Create the virtual environment expected by `run.cmd`, install the dependencies,
and copy the example configuration:

```powershell
python -m venv ..\env314
..\env314\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item setting.example.json setting.json
```

Edit `setting.json` with the real RTSP URL, then start the server on Windows:

```powershell
.\run.cmd
```

`run.cmd` switches to the project directory, activates `..\env314`, and runs
`python app.py`. Command-line options are forwarded to the application. For
example:

```powershell
.\run.cmd --host 0.0.0.0 --port 9000
```

To start the service manually instead, activate the virtual environment and run
the Python entry point:

```powershell
..\env314\Scripts\Activate.ps1
python app.py
```

Open <http://127.0.0.1:8080>. Stop the process with `Ctrl+C`.

The web page provides camera selection, live connection state, frame rate,
start/stop controls, and an editor for adding or changing cameras. Camera edits
are saved to `setting.json` immediately and do not require a server restart.

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
An empty `"cameras": []` list is valid; start the service and use **Add camera**
in the web UI to create the first entry.
For a single camera, this compact legacy format is also accepted:

```json
{
  "rtsp_url": "rtsp://username:password@192.168.1.10:554/stream1"
}
```

Listen on another address or port without changing the file:

```powershell
.\run.cmd --host 0.0.0.0 --port 9000
```

Binding to `0.0.0.0` exposes the page to the local network. The service has no
login layer, so only do this on a trusted network or place it behind an
authenticated reverse proxy.

## HTTP endpoints

- `GET /`: viewer UI
- `GET /api/cameras`: camera state and editable camera settings
- `POST /api/cameras`: add a camera and persist it to the settings file
- `PUT /api/cameras/<camera-id>`: update and persist a camera
- `GET /stream/<camera-id>.mjpg`: browser-compatible MJPEG stream
- `POST /api/cameras/<camera-id>/start`: start or reconnect a camera
- `POST /api/cameras/<camera-id>/stop`: stop a camera
- `GET /health`: service and camera health summary

The camera API and web editor expose complete RTSP URLs, including embedded
credentials. Keep the service on a trusted network or put it behind an
authenticated reverse proxy.

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

To create a standalone Windows executable (including the static web assets),
run `build.cmd`. The result is written to `dist\rtsp-replayer.exe`.
