"""
JARVIS — Holographic Display + Remote API
Lokaler HTTP-Server + Three.js im Browser (Edge/Chrome App-Modus).
Zeigt Echtzeit-Audio-Level, Zustand und Gesprächsverlauf.

Netzwerk-Zugriff: http://<IP>:5420 — von jedem Gerät im LAN erreichbar.
API-Endpoints:
    GET  /data          — Echtzeit-Daten (State, Bars, Messages)
    POST /ask           — Text-Eingabe  {"text":"..."} → {"reply":"..."}
    POST /voice         — Audio-Eingabe (webm/wav) → {"text":"...","reply":"..."}
"""

import io
import json
import math
import os
import queue
import socket
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

_HTML_PATH = Path(__file__).parent / "hologram.html"
_PORT      = 5420
_USERNAME  = os.getenv("USERNAME", os.getenv("USER", "")).upper()

_CHROMIUM_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def local_ip() -> str:
    """Lokale Netzwerk-IP für LAN-Zugriff."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def _screen_size():
    try:
        import ctypes
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def _launch_app_window(url: str) -> bool:
    sw, sh = _screen_size()
    for path in _CHROMIUM_CANDIDATES:
        if os.path.exists(path):
            try:
                subprocess.Popen([
                    path, f"--app={url}",
                    f"--window-size={sw},{sh}",
                    f"--window-position=0,0",
                    "--start-fullscreen",        # F11-Vollbild
                    "--no-first-run", "--disable-default-apps",
                    "--disable-infobars", "--no-default-browser-check",
                    "--disable-extensions",
                    "--force-device-scale-factor=1",  # kein OS-Scaling
                ])
                return True
            except Exception:
                continue
    webbrowser.open(url, new=2, autoraise=True)
    return False


class HologramDisplay:
    BARS     = 28
    MAX_MSGS = 6   # Letzte N Nachrichten sichtbar

    def __init__(self):
        self._state   = "idle"
        self._level   = 0.0
        self._bars    = [0.0] * self.BARS
        self._msgs    = []          # [{role, text, ts}]
        self._q: queue.Queue = queue.Queue()
        self._server: Optional[HTTPServer] = None
        self._ready   = threading.Event()
        self._thread  = threading.Thread(target=self._run, daemon=True,
                                         name="jarvis-hologram")
        # Remote-API: Kernel + Groq-Client (gesetzt via set_kernel)
        self._kernel   = None
        self._groq     = None
        self._api_lock = threading.Lock()

    # ── öffentliche API ───────────────────────────────────────────────────────

    def set_kernel(self, kernel, groq_client=None):
        """Verbindet Kernel + Groq-Client für Remote-Zugriff (/ask, /voice)."""
        self._kernel = kernel
        self._groq   = groq_client

    def start(self) -> bool:
        try:
            self._thread.start()
            ok = self._ready.wait(timeout=6)
            if ok:
                threading.Timer(0.6, lambda: _launch_app_window(
                    f"http://localhost:{_PORT}"
                )).start()
            return ok
        except Exception:
            return False

    def set_state(self, state: str):
        self._q.put(state)

    def update_level(self, level: float):
        lvl = max(0.0, min(1.0, float(level)))
        self._level = lvl
        t = time.time()
        n = self.BARS
        self._bars = [
            lvl * abs(math.sin(t * 9 + i * 0.55) * math.sin(t * 4.1 + i * 0.22))
            * math.exp(-abs(i - n / 2) / (n / 3))
            for i in range(n)
        ]

    def add_message(self, role: str, text: str):
        """Fügt eine Gesprächszeile hinzu (role: 'JARVIS' oder 'SIR')."""
        if not text or not text.strip():
            return
        self._msgs.append({
            "role": role,
            "text": text.strip(),
            "ts":   time.time(),
        })
        if len(self._msgs) > self.MAX_MSGS:
            self._msgs = self._msgs[-self.MAX_MSGS:]

    def open_browser(self):
        _launch_app_window(f"http://localhost:{_PORT}")

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass

    # ── HTTP-Server ───────────────────────────────────────────────────────────

    def _run(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            # ── GET ───────────────────────────────────────────────────────

            def do_GET(self):
                try:
                    while True:
                        parent._state = parent._q.get_nowait()
                except queue.Empty:
                    pass

                if self.path == "/data":
                    now  = time.time()
                    msgs = [
                        {"role": m["role"], "text": m["text"],
                         "age": round(now - m["ts"], 1)}
                        for m in parent._msgs
                    ]
                    body = json.dumps({
                        "state": parent._state,
                        "level": round(parent._level, 4),
                        "bars":  [round(b, 4) for b in parent._bars],
                        "msgs":  msgs,
                        "user":  _USERNAME,
                    }).encode()
                    self._json(body)

                elif self.path in ("/", "/index.html"):
                    if _HTML_PATH.exists():
                        html = _HTML_PATH.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type",   "text/html; charset=utf-8")
                        self.send_header("Content-Length",  str(len(html)))
                        self.end_headers()
                        self.wfile.write(html)
                    else:
                        self.send_response(404); self.end_headers()
                else:
                    self.send_response(404); self.end_headers()

            # ── POST — Remote API ─────────────────────────────────────────

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))

                if self.path == "/ask":
                    self._handle_ask(length)
                elif self.path == "/voice":
                    self._handle_voice(length)
                else:
                    self.send_response(404); self.end_headers()

            def _handle_ask(self, length):
                """POST /ask — Text → JARVIS-Antwort."""
                if not parent._kernel:
                    self._error(503, "Kernel nicht verfügbar")
                    return
                try:
                    data = json.loads(self.rfile.read(length))
                except Exception:
                    self._error(400, "Ungültiges JSON")
                    return
                text = (data.get("text") or "").strip()
                if not text:
                    self._error(400, "Kein Text")
                    return

                with parent._api_lock:
                    parent.add_message("SIR", text)
                    parent.set_state("thinking")
                    try:
                        reply = parent._kernel.process(text)
                    except Exception as e:
                        parent.set_state("idle")
                        self._error(500, str(e))
                        return
                    parent.add_message("JARVIS", reply)
                    parent.set_state("idle")

                self._json(json.dumps({
                    "reply": reply,
                }).encode())

            def _handle_voice(self, length):
                """POST /voice — Audio (webm/wav) → Transkription + JARVIS-Antwort."""
                if not parent._kernel or not parent._groq:
                    self._error(503, "Kernel/Groq nicht verfügbar")
                    return
                audio_data = self.rfile.read(length)
                if not audio_data:
                    self._error(400, "Keine Audiodaten")
                    return

                ct = self.headers.get("Content-Type", "audio/webm")
                ext = "webm" if "webm" in ct else "wav"

                with parent._api_lock:
                    # Transkription via Whisper
                    try:
                        buf = io.BytesIO(audio_data)
                        result = parent._groq.audio.transcriptions.create(
                            file=(f"speech.{ext}", buf),
                            model="whisper-large-v3-turbo",
                            response_format="text",
                        )
                        text = (result or "").strip()
                    except Exception as e:
                        self._error(500, f"Whisper: {e}")
                        return

                    if not text:
                        self._json(json.dumps({
                            "text": "", "reply": "",
                        }).encode())
                        return

                    parent.add_message("SIR", text)
                    parent.set_state("thinking")
                    try:
                        reply = parent._kernel.process(text)
                    except Exception as e:
                        parent.set_state("idle")
                        self._error(500, str(e))
                        return
                    parent.add_message("JARVIS", reply)
                    parent.set_state("idle")

                self._json(json.dumps({
                    "text":  text,
                    "reply": reply,
                }).encode())

            # ── CORS Preflight ────────────────────────────────────────────

            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            # ── Helpers ───────────────────────────────────────────────────

            def _json(self, body: bytes):
                self.send_response(200)
                self.send_header("Content-Type",   "application/json")
                self.send_header("Content-Length",  str(len(body)))
                self.send_header("Cache-Control",   "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _error(self, code: int, msg: str):
                body = json.dumps({"error": msg}).encode()
                self.send_response(code)
                self.send_header("Content-Type",   "application/json")
                self.send_header("Content-Length",  str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

        try:
            server = HTTPServer(("0.0.0.0", _PORT), Handler)
            self._server = server
            self._ready.set()
            server.serve_forever()
        except Exception:
            self._ready.set()
