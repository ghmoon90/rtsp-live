@echo off
python -m pip install pyinstaller
pyinstaller app.py --onefile --name rtsp-replayer --add-data "static;static"
