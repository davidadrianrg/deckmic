#!/usr/bin/env python3
"""
DeckMic — Usa tu móvil Android como micrófono inalámbrico + dictado Whisper para Linux/SteamOS.

Flujo: el móvil captura audio (navegador, PWA) → WebSocket TLS → este servidor
detecta fin de frase (VAD) → transcribe con whisper.cpp (local, offline) →
escribe el texto en la ventana enfocada (ydotool/xdotool), lo copia al
portapapeles, o ejecuta un comando mapeado.

Solo usa la librería estándar de Python. Sin dependencias pip.

Uso:
    python3 server.py                 # arranca con config.json (lo crea si falta)
    python3 server.py --check         # diagnóstico: whisper, modelo, escritor, IPs, PIN
    python3 server.py --port 8443     # cambia puerto

Config (config.json, se genera al primer arranque):
    pin                 PIN de 6 dígitos que se pide en el móvil
    model               ruta del modelo GGML de whisper.cpp
    lang                idioma para Whisper: "auto" o "es", "en"...
    writer              "auto" | "ydotool" | "xdotool" | "debug" | "none"
    commands            alias de voz -> comando shell (modo Comando)
    vad_threshold_db    sensibilidad del silencio (dBFS, -50 sensible .. -30 insensible)
"""

import base64
import difflib
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

try:
    from socketserver import ThreadingMixIn
except ImportError:  # pragma: no cover
    ThreadingMixIn = object

VERSION = "0.2.7"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WWW_DIR = os.path.join(APP_DIR, "www")
CERT_DIR = os.path.join(APP_DIR, "certs")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

log = logging.getLogger("deckmic")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "pin": "",
    "port": 8443,
    "tls": True,
    "model": os.path.join(APP_DIR, "models", "ggml-large-v3-turbo-q5_0.bin"),
    "whisper_cli": "whisper-cli",
    "lang": "auto",
    "beam_size": 5,
    "prompt": "",
    "writer": "auto",
    "ydotool": "ydotool",
    "xdotool": "xdotool",
    "wl_copy": "wl-copy",
    "xclip": "xclip",
    "vad_threshold_db": -42.0,
    "vad_silence_ms": 1300,
    "min_speech_ms": 350,
    "max_session_s": 120,
    "whisper_timeout_s": 600,
    "normalize_audio": True,
    "commands": {
        "ejemplo hola": "echo 'Edita commands en config.json'",
    },
}


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
        except Exception as e:
            log.warning("config.json ilegible (%s); uso valores por defecto", e)
    else:
        # arrancar con comando personalizado de ejemplo desactivado
        cfg["commands"] = {}
    cfg["_path"] = path
    return cfg


def save_default_config(path):
    cfg = dict(DEFAULT_CONFIG)
    cfg["pin"] = "".join(secrets.choice("0123456789") for _ in range(6))
    cfg["commands"] = {}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return cfg


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def local_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


def rms_dbfs(pcm_s16le):
    """RMS en dBFS de un bloque PCM int16 little-endian."""
    if len(pcm_s16le) < 2:
        return -96.0
    n = len(pcm_s16le) // 2
    total = 0
    for (v,) in struct.iter_unpack("<h", pcm_s16le[: n * 2]):
        total += v * v
    rms = (total / n) ** 0.5
    if rms <= 0:
        return -96.0
    return 20.0 * (rms / 32768.0).__log10__() if False else 20.0 * _log10(rms / 32768.0)


def _log10(x):
    import math

    return math.log10(x)


def pcm_to_wav(pcm, rate=16000):
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def normalize_pcm(pcm, target=0.55):
    """Reescala el pico si el audio clipa o es muy bajo."""
    n = len(pcm) // 2
    if n == 0:
        return pcm
    peak = 1
    for (v,) in struct.iter_unpack("<h", pcm):
        a = abs(v)
        if a > peak:
            peak = a
    if peak >= 32767 * 0.98 or peak < 32767 * 0.06:
        gain = (32767 * target) / max(peak, 1)
        if 0.2 < gain < 15:
            out = bytearray(len(pcm))
            for i, (v,) in enumerate(struct.iter_unpack("<h", pcm)):
                val = int(v * gain)
                val = 32767 if val > 32767 else (-32768 if val < -32768 else val)
                struct.pack_into("<h", out, i * 2, val)
            return bytes(out)
    return pcm


def strip_accents(s):
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn"
    )


def norm_command_key(s):
    s = strip_accents(s)
    for ch in "¿?¡!.,;:()[]\"'":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def ascii_sanitize(s):
    """ydotool solo mapea ASCII a keycodes (tool_type.c): los bytes UTF-8
    multibyte indexan fuera de su tabla. Devuelve el texto sin acentos
    (Á→A, ñ→n) y sin caracteres no ASCII (¿, ¡, emojis…)."""
    import unicodedata

    out = []
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.category(ch) != "Mn":
            out.append(ch)
    return "".join(out).encode("ascii", "ignore").decode("ascii")


# --------------------------------------------------------------------------
# Whisper
# --------------------------------------------------------------------------

class WhisperEngine:
    def __init__(self, cli, model, lang="auto", beam_size=5, prompt="", timeout=600):
        self.cli = cli
        self.model = model
        self.lang = lang
        self.beam_size = beam_size
        self.prompt = prompt
        self.timeout = int(timeout)
        self.lock = threading.Lock()
        self.ok = bool(shutil.which(cli) or os.path.isfile(cli))
        self.model_ok = os.path.isfile(model) if model else False
        if self.ok and os.path.isfile(cli):
            self.cli = os.path.abspath(cli)

    def available(self):
        return self.ok and self.model_ok

    def transcribe(self, wav_bytes, timeout=None, prompt=None):
        if not self.available():
            raise RuntimeError("whisper-cli o modelo no disponibles (ejecuta install.sh)")
        if timeout is None:
            timeout = self.timeout
        if prompt is None:
            prompt = self.prompt
        with self.lock:
            fd, wav_path = tempfile.mkstemp(prefix="deckmic-", suffix=".wav")
            out_prefix = wav_path[:-4]
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(wav_bytes)
                cmd = [
                    self.cli, "-m", self.model, "-f", wav_path,
                    "-l", self.lang or "auto", "-np", "-otxt", "-of", out_prefix,
                ]
                if self.beam_size and self.beam_size > 1:
                    cmd += ["-bs", str(self.beam_size)]
                if prompt:
                    cmd += ["--prompt", prompt]
                t0 = time.time()
                p = subprocess.run(cmd, capture_output=True, timeout=timeout)
                dt = time.time() - t0
                txt_file = out_prefix + ".txt"
                text = ""
                if os.path.exists(txt_file):
                    with open(txt_file, "r", encoding="utf-8", errors="ignore") as f:
                        text = " ".join(f.read().split())
                if p.returncode != 0 and not text:
                    err = (p.stderr or p.stdout or b"").decode("utf-8", "ignore")[-300:]
                    raise RuntimeError(f"whisper-cli falló: {err}")
                log.info("whisper %.1fs: %s", dt, text[:120])
                return text.strip()
            finally:
                for p_ in (wav_path, out_prefix + ".txt"):
                    try:
                        if os.path.exists(p_):
                            os.remove(p_)
                    except OSError:
                        pass


# --------------------------------------------------------------------------
# Escritores (salida de texto al sistema)
# --------------------------------------------------------------------------

class Writer:
    kind = "none"

    def type_text(self, text):
        pass

    def press_enter(self):
        pass

    def copy(self, text):
        return False


class YdotoolWriter(Writer):
    kind = "ydotool"

    def __init__(self, bin_path):
        self.bin = bin_path
        self.env = dict(os.environ)
        # ydotoold (master/1.0.4) escucha en $XDG_RUNTIME_DIR/.ydotool_socket y
        # el cliente ydotool lee YDOTOOL_SOCKET. Los servicios de usuario de
        # systemd siempre definen XDG_RUNTIME_DIR; el fallback es el que usa el
        # propio cliente cuando no existe (Client/ydotool.c).
        self.env.setdefault(
            "YDOTOOL_SOCKET",
            os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", ".ydotool_socket"),
        )

    def _run(self, args, input_bytes=None):
        return subprocess.run([self.bin] + args, env=self.env, input=input_bytes,
                              capture_output=True, timeout=30)

    def type_text(self, text):
        t = ascii_sanitize(text)
        if not t:
            return
        if t != text:
            log.info("ydotool: texto con no-ASCII, tecleo versión sin acentos "
                     "(el exacto va al portapapeles en modo clip)")
        # "-f -" lee de stdin: escape desactivado por defecto (los '\' del texto
        # no se interpretan). Como argumento, en cambio, ydotool activaría el
        # escape y también rompería con getopt en 1.0.4.
        r = self._run(["type", "-f", "-"], input_bytes=t.encode("utf-8"))
        if r.returncode != 0:
            log.warning("ydotool type rc=%d: %s", r.returncode,
                        r.stderr.decode("utf-8", "ignore")[:200])

    def press_enter(self):
        self._run(["key", "28:1", "28:0"])


class XdotoolWriter(Writer):
    kind = "xdotool"

    def __init__(self, bin_path):
        self.bin = bin_path

    def type_text(self, text):
        subprocess.run([self.bin, "type", "--delay", "40", "--", text],
                       capture_output=True, timeout=30)

    def press_enter(self):
        subprocess.run([self.bin, "key", "Return"], capture_output=True, timeout=30)


class DebugWriter(Writer):
    """No toca el sistema: registra lo que se escribiría (útil para probar)."""
    kind = "debug"

    def __init__(self):
        os.makedirs(os.path.join(APP_DIR, "data"), exist_ok=True)
        self.path = os.path.join(APP_DIR, "data", "typed.log")

    def _log(self, tag, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} [{tag}] {text}\n")

    def type_text(self, text):
        self._log("type", text)

    def press_enter(self):
        self._log("enter", "<Enter>")

    def copy(self, text):
        self._log("clip", text)
        return True


class NoneWriter(Writer):
    kind = "none"


def pick_writer(cfg):
    mode = (cfg.get("writer") or "auto").lower()
    if mode == "none":
        return NoneWriter()
    if mode == "debug":
        return DebugWriter()
    if mode == "ydotool":
        return YdotoolWriter(cfg["ydotool"])
    if mode == "xdotool":
        return XdotoolWriter(cfg["xdotool"])
    # auto
    yd_path = os.path.expanduser(str(cfg.get("ydotool") or ""))
    yd = yd_path if (yd_path and os.path.isfile(yd_path)) else shutil.which("ydotool")
    if yd:
        return YdotoolWriter(yd)
    xd_path = os.path.expanduser(str(cfg.get("xdotool") or ""))
    xd = xd_path if (xd_path and os.path.isfile(xd_path)) else shutil.which("xdotool")
    if xd and os.environ.get("DISPLAY"):
        return XdotoolWriter(xd)
    log.warning("sin ydotool/xdotool: uso modo debug (texto en data/typed.log)")
    return DebugWriter()


def clipboard_copy(cfg, text):
    wl = shutil.which(cfg.get("wl_copy", "wl-copy"))
    if wl:
        p = subprocess.run([wl], input=text.encode("utf-8"), capture_output=True)
        return p.returncode == 0
    xc = shutil.which(cfg.get("xclip", "xclip"))
    if xc:
        p = subprocess.run([xc, "-selection", "clipboard"], input=text.encode("utf-8"),
                           capture_output=True)
        return p.returncode == 0
    return False


# --------------------------------------------------------------------------
# WebSocket (RFC6455 mínimo, lado servidor)
# --------------------------------------------------------------------------

class WSError(Exception):
    pass


def ws_accept_key(key):
    digest = hashlib.sha1((key + WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def ws_encode(payload, opcode=0x1):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
        opcode = 0x1
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    return bytes(header) + payload


class WSConn:
    """Conexión WebSocket sobre el socket TLS del handler."""

    def __init__(self, rfile, wfile, send_lock=None):
        self.rfile = rfile
        self.wfile = wfile
        self.lock = send_lock or threading.Lock()
        self.closed = False

    def _read_exact(self, n):
        data = self.rfile.read(n)
        if data is None or len(data) < n:
            raise WSError("conexión cerrada")
        return data

    def read_message(self):
        """Devuelve (opcode, payload) ensamblando fragmentos. opcode final 1/2/8/9."""
        payload = bytearray()
        opcode0 = None
        while True:
            b1, b2 = self._read_exact(2)
            fin = b1 & 0x80
            op = b1 & 0x0F
            masked = b2 & 0x80
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read_exact(8))[0]
            if ln > 8 * 1024 * 1024:
                raise WSError("frame demasiado grande")
            mask = self._read_exact(4) if masked else None
            chunk = bytearray(self._read_exact(ln))
            if mask:
                for i in range(ln):
                    chunk[i] ^= mask[i & 3]
            if op in (0x8, 0x9, 0xA):  # control: sin fragmentación
                return op, bytes(chunk)
            if opcode0 is None:
                opcode0 = op
            payload += chunk
            if fin:
                return opcode0, bytes(payload)

    def send(self, payload, opcode=0x1):
        if self.closed:
            return
        data = ws_encode(payload, opcode)
        with self.lock:
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except Exception:
                self.closed = True

    def send_json(self, obj):
        self.send(json.dumps(obj, ensure_ascii=False))

    def close(self, code=1000):
        try:
            self.send(struct.pack(">H", code), opcode=0x8)
        except Exception:
            pass
        self.closed = True


# --------------------------------------------------------------------------
# Sesión de micrófono (VAD + transcripción + acción)
# --------------------------------------------------------------------------

class ServerState:
    def __init__(self, cfg, engine, writer):
        self.cfg = cfg
        self.engine = engine
        self.writer = writer
        self.started = time.time()
        self.sessions = 0
        self.last_result = None
        self.last_result_at = None
        self.busy = False  # transcripción en curso


class MicSession:
    """Estado de un stream de audio de un cliente."""

    def __init__(self, state, ws):
        self.state = state
        self.ws = ws
        self.audio = bytearray()
        self.speech_ms = 0
        self.silence_ms = 0
        self.vad_mode = False
        self.mode = "type"
        self.t0 = time.time()
        self.in_speech = False

    # -- control ----------------------------------------------------------
    def start(self, msg):
        self.mode = msg.get("mode", "type")
        self.vad_mode = bool(msg.get("vad", False))
        self._reset()

    def _reset(self):
        self.audio = bytearray()
        self.speech_ms = 0
        self.silence_ms = 0
        self.in_speech = False
        self.t0 = time.time()

    def cancel(self):
        self._reset()

    # -- audio ------------------------------------------------------------
    def on_audio(self, pcm):
        if time.time() - self.t0 > self.state.cfg["max_session_s"]:
            self.finalize(auto=True)
            self._reset()
            return
        if not self.vad_mode:
            self.audio += pcm
            return
        db = rms_dbfs(pcm)
        thr = self.state.cfg["vad_threshold_db"]
        chunk_ms = (len(pcm) // 2) / 16.0  # 16 kHz mono
        if db >= thr:
            self.speech_ms += chunk_ms
            self.silence_ms = 0
            self.in_speech = True
        elif self.in_speech:
            self.silence_ms += chunk_ms
        self.audio += pcm
        if (
            self.in_speech
            and self.speech_ms >= self.state.cfg["min_speech_ms"]
            and self.silence_ms >= self.state.cfg["vad_silence_ms"]
        ):
            self.finalize(auto=True)
            self._reset()  # en modo walkie se puede seguir hablando

    # -- fin de frase -----------------------------------------------------
    def finalize(self, auto=False):
        cfg = self.state.cfg
        dur_ms = (len(self.audio) // 2) / 16.0
        if dur_ms < cfg["min_speech_ms"]:
            if not auto:
                self.ws.send_json({"type": "info", "msg": "audio demasiado corto"})
            return
        self.state.sessions += 1
        self.state.busy = True
        self.ws.send_json({"type": "state", "value": "transcribing"})
        try:
            pcm = bytes(self.audio)
            if cfg.get("normalize_audio", True):
                pcm = normalize_pcm(pcm)
            wav = pcm_to_wav(pcm)
            # modo comando: sesgar la transcripción hacia los alias configurados
            prompt = self.state.engine.prompt
            if self.mode == "cmd" and not prompt:
                aliases = list((cfg.get("commands") or {}).keys())
                if aliases:
                    prompt = ", ".join(aliases)[:224]
            text = self.state.engine.transcribe(wav, prompt=prompt)
            self.state.last_result = text
            self.state.last_result_at = datetime.now().isoformat(timespec="seconds")
            result = {"type": "result", "text": text, "mode": self.mode}
            if text:
                extra = self._act(text)
                if extra:
                    result.update(extra)
            self.ws.send_json(result)
        except Exception as e:
            log.exception("error transcribiendo")
            self.ws.send_json({"type": "error", "msg": str(e)[:200]})
        finally:
            self.state.busy = False
            self.ws.send_json({"type": "state", "value": "listening" if self.vad_mode else "idle"})

    def _act(self, text):
        w = self.state.writer
        mode = self.mode
        if mode == "clip":
            ok = w.copy(text) or clipboard_copy(self.state.cfg, text)
            return {"clip": ok}
        if mode == "cmd":
            return self._run_command(text)
        if mode in ("type", "enter"):
            w.type_text(text)
            if mode == "enter":
                time.sleep(0.15)
                w.press_enter()
            return {"typed": True}
        return {}

    def _run_command(self, text):
        cfg = self.state.cfg
        cmds = cfg.get("commands") or {}
        key = norm_command_key(text)
        best, best_score = None, 0.0
        for alias, shell in cmds.items():
            a = norm_command_key(alias)
            if a == key:
                best, best_score = (alias, shell), 1.0
                break
            # alias contenido en lo dicho ("por favor abrir firefox" → "abrir firefox")
            if a and a in key:
                best, best_score = (alias, shell), 0.9
                continue
            # difuso: tolera errores de transcripción ("abril fight folks" ≈ "abrir firefox")
            import difflib
            score = difflib.SequenceMatcher(None, a, key).ratio()
            if score > best_score:
                best, best_score = (alias, shell), score
        if not best or best_score < 0.55:
            return {"cmd": None, "msg": "comando no reconocido (revísalo en config.json)"}
        alias, shell = best
        log.info("comando [%s] -> %s", alias, shell)
        try:
            p = subprocess.run(shell, shell=True, capture_output=True, timeout=30,
                               cwd=os.path.expanduser("~"))
            out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "ignore")
            return {"cmd": alias, "rc": p.returncode, "output": out[-400:]}
        except Exception as e:
            return {"cmd": alias, "rc": -1, "output": str(e)[:200]}


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DeckMic/" + VERSION

    # silenciar log por defecto; usamos logging propio
    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    @property
    def state(self):
        return self.server.state

    # ------------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        # ---- WebSocket upgrade ------------------------------------------
        conn = (self.headers.get("Connection") or "").lower()
        if "upgrade" in conn and (self.headers.get("Upgrade") or "").lower() == "websocket":
            if not self._pin_ok(q):
                self._json(401, {"error": "PIN incorrecto"})
                return
            self._ws_handshake_and_serve()
            return

        if path == "/health":
            self._json(200, {"ok": True, "version": VERSION})
        elif path == "/api/status":
            if not self._pin_ok(q):
                self._json(401, {"error": "PIN incorrecto"})
                return
            self._json(200, self._status())
        elif path in ("/", "/index.html"):
            self._file("index.html")
        else:
            self._file(path.lstrip("/"))

    # ------------------------------------------------------------------
    def _pin_ok(self, q):
        pin = (q.get("pin") or [""])[0]
        return secrets.compare_digest(pin, self.state.cfg.get("pin", ""))

    def _status(self):
        st = self.state
        model = st.cfg.get("model", "")
        return {
            "ok": True,
            "version": VERSION,
            "hostname": socket.gethostname(),
            "ips": local_ips(),
            "port": st.cfg.get("port"),
            "tls": bool(st.cfg.get("tls")),
            "whisper": st.engine.ok,
            "model_ok": st.engine.model_ok,
            "model": os.path.basename(model) if model else "",
            "model_mb": round(os.path.getsize(model) / 1e6) if st.engine.model_ok else 0,
            "writer": st.writer.kind,
            "uptime_s": int(time.time() - st.started),
            "sessions": st.sessions,
            "busy": st.busy,
            "last_result": st.last_result,
            "last_result_at": st.last_result_at,
        }

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, rel):
        rel = os.path.normpath(rel).lstrip("/")
        if rel in ("", "."):
            rel = "index.html"
        full = os.path.join(WWW_DIR, rel)
        if not os.path.realpath(full).startswith(os.path.realpath(WWW_DIR)) or not os.path.isfile(full):
            self._json(404, {"error": "no encontrado"})
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            body = f.read()
        if rel == "sw.js":
            # versiona la caché del service worker: bump de VERSION = shell nueva
            body = body.replace(b"__VERSION__", VERSION.encode())
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ext in (".html", ".js") else "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    def _ws_handshake_and_serve(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._json(400, {"error": "falta Sec-WebSocket-Key"})
            return
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {ws_accept_key(key)}\r\n"
            "\r\n"
        )
        try:
            self.connection.settimeout(300)
            self.wfile.write(resp.encode())
            self.wfile.flush()
        except Exception:
            return
        ws = WSConn(self.rfile, self.wfile)
        session = MicSession(self.state, ws)
        ws.send_json({"type": "hello", "version": VERSION,
                      "writer": self.state.writer.kind,
                      "whisper_ok": self.state.engine.available()})
        try:
            while True:
                op, payload = ws.read_message()
                if op == 0x8:  # close
                    break
                if op == 0x9:  # ping -> pong
                    ws.send(payload, opcode=0xA)
                    continue
                if op == 0xA:
                    continue
                if op == 0x1:
                    try:
                        msg = json.loads(payload.decode("utf-8"))
                    except Exception:
                        continue
                    mtype = msg.get("type")
                    if mtype == "start":
                        session.start(msg)
                        ws.send_json({"type": "state",
                                      "value": "listening" if session.vad_mode else "recording"})
                    elif mtype == "end":
                        session.finalize()
                        session._reset()
                        ws.send_json({"type": "state", "value": "idle"})
                    elif mtype == "cancel":
                        session.cancel()
                        ws.send_json({"type": "state", "value": "idle"})
                elif op == 0x2:
                    session.on_audio(payload)
        except (WSError, socket.timeout, ConnectionError, ssl.SSLError, OSError):
            pass
        except Exception:
            log.exception("error en sesión WS")
        finally:
            try:
                ws.close()
            except Exception:
                pass
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            self.connection.close()
            log.info("sesión cerrada (%s)", self.address_string())


class DeckMicServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------

def ensure_cert(cfg):
    if not cfg.get("tls", True):
        return None, None
    os.makedirs(CERT_DIR, exist_ok=True)
    crt = os.path.join(CERT_DIR, "deckmic.crt")
    key = os.path.join(CERT_DIR, "deckmic.key")
    if os.path.exists(crt) and os.path.exists(key):
        return crt, key
    sans = ",".join(
        ["DNS:localhost", "DNS:deckmic.local"]
        + [f"IP:{ip}" for ip in local_ips()]
        + ["IP:127.0.0.1"]
    )
    cmd = [
        "openssl", "req", "-x509", "-newkey", "ec",
        "-pkeyopt", "ec_paramgen_curve:prime256v1",
        "-days", "3650", "-nodes", "-subj", "/CN=deckmic.local",
        "-addext", f"subjectAltName={sans}",
        "-keyout", key, "-out", crt,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        os.chmod(key, 0o600)
        log.info("certificado autofirmado generado en %s", crt)
        return crt, key
    except Exception as e:
        log.error("no pude generar certificado TLS (%s). "
                  "Arranca con --no-tls solo para pruebas locales.", e)
        return None, None


# --------------------------------------------------------------------------
# Diagnóstico
# --------------------------------------------------------------------------

def run_check(cfg):
    print(f"DeckMic v{VERSION} — diagnóstico\n" + "-" * 46)
    eng = WhisperEngine(cfg["whisper_cli"], cfg["model"], cfg.get("lang", "auto"))
    print(f"whisper-cli : {'OK' if eng.ok else 'NO ENCONTRADO'} ({cfg['whisper_cli']})")
    print(f"modelo      : {'OK' if eng.model_ok else 'FALTA'} ({cfg['model']})")
    if eng.model_ok:
        print(f"              {os.path.getsize(cfg['model'])/1e6:.0f} MB")
    w = pick_writer(cfg)
    print(f"escritor    : {w.kind}")
    clip = shutil.which(cfg.get("wl_copy", "wl-copy")) or shutil.which(cfg.get("xclip", "xclip"))
    print(f"portapapeles: {'OK' if clip else 'NO (instala wl-clipboard)'}")
    print(f"idioma      : {cfg.get('lang')}")
    print(f"PIN         : {cfg.get('pin')}")
    for ip in local_ips():
        scheme = "https" if cfg.get("tls", True) else "http"
        print(f"URL móvil   : {scheme}://{ip}:{cfg['port']}/  (PIN: {cfg.get('pin')})")
    print("\nComandos de voz configurados:")
    for alias in (cfg.get("commands") or {}):
        print(f"  - {alias}")
    if not (cfg.get("commands") or {}):
        print("  (ninguno — añade en config.json)")
    if w.kind == "debug":
        print("\nAVISO: sin ydotool instalado, el texto solo se registra en data/typed.log")
    return 0


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    import argparse

    ap = argparse.ArgumentParser(description="DeckMic server")
    ap.add_argument("--config", default=os.path.join(APP_DIR, "config.json"))
    ap.add_argument("--port", type=int, help="puerto (por defecto 8443)")
    ap.add_argument("--no-tls", action="store_true", help="sin TLS (solo pruebas locales)")
    ap.add_argument("--check", action="store_true", help="diagnóstico y salir")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    first_run = not os.path.exists(args.config)
    cfg = load_config(args.config)
    if first_run or not cfg.get("pin"):
        cfg = save_default_config(args.config)
        log.info("config creado: %s", args.config)
    if args.port:
        cfg["port"] = args.port
    if args.no_tls:
        cfg["tls"] = False

    if args.check:
        sys.exit(run_check(cfg))

    engine = WhisperEngine(cfg["whisper_cli"], cfg["model"], cfg.get("lang", "auto"),
                           int(cfg.get("beam_size", 5)), cfg.get("prompt", ""),
                           int(cfg.get("whisper_timeout_s", 600)))
    writer = pick_writer(cfg)
    if not engine.available():
        log.warning("whisper no listo (cli=%s modelo=%s) — ejecuta install.sh",
                    engine.ok, engine.model_ok)

    httpd = DeckMicServer(("0.0.0.0", cfg["port"]), Handler)
    httpd.state = ServerState(cfg, engine, writer)

    if cfg.get("tls", True):
        crt, key = ensure_cert(cfg)
        if crt:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(crt, key)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        else:
            log.error("TLS no disponible; continúo SIN TLS (getUserMedia del navegador "
                      "no funcionará desde otro dispositivo)")

    scheme = "https" if cfg.get("tls", True) else "http"
    log.info("DeckMic v%s escuchando en %s://0.0.0.0:%d (escritor: %s)",
             VERSION, scheme, cfg["port"], writer.kind)
    for ip in local_ips():
        log.info("  móvil → %s://%s:%d/  PIN: %s", scheme, ip, cfg["port"], cfg["pin"])
    log.info("  Ctrl+C para parar")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("adiós")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
