/* DeckMic — app móvil (PWA). Conecta por WSS con el servidor del PC. */
"use strict";

const $ = (id) => document.getElementById(id);
const qs = new URLSearchParams(location.search);
const store = {
  get pin() { return localStorage.getItem("deckmic-pin") || ""; },
  set pin(v) { localStorage.setItem("deckmic-pin", v); },
  get mode() { return localStorage.getItem("deckmic-mode") || "type"; },
  set mode(v) { localStorage.setItem("deckmic-mode", v); },
  get space() { return localStorage.getItem("deckmic-space") === "1"; },
  set space(v) {
    if (v) localStorage.setItem("deckmic-space", "1");
    else localStorage.removeItem("deckmic-space");
  },
  get keepAwake() { return localStorage.getItem("deckmic-keepawake") !== "0"; },
  set keepAwake(v) {
    if (v) localStorage.removeItem("deckmic-keepawake");
    else localStorage.setItem("deckmic-keepawake", "0");
  },
};

/* ---------------- estado global ---------------- */
let ws = null;
let status = "idle";         // idle | recording | listening | transcribing
let mode = store.mode;       // type | enter | clip | cmd
let vad = false;             // modo manos libres
let micOn = false;           // usuario mantiene pulsado el botón
let audioCtx = null, workletNode = null, mediaStream = null;
let lastTranscript = "";
let serverVersion = "";
let wakeLock = null;
let micHeldMs = 0;

/* ---------------- utilidades ---------------- */
function setStatus(txt, cls = "") {
  $("status").textContent = txt;
  $("status").className = "status " + cls;
}
function setDot(ok) {
  const d = $("conn-dot");
  d.className = "dot " + (ok === true ? "ok" : ok === false ? "bad" : "");
  $("conn-txt").textContent = ok === true ? "conectado" : ok === false ? "desconectado" : "conectando…";
}
function fmtTime() {
  return new Date().toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
}
function addTranscript(item) {
  const list = $("tr-list");
  const div = document.createElement("div");
  div.className = "tr-item" + (item.err ? " err" : item.ok ? " ok" : "");
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `${fmtTime()} · ${item.label}`;
  const txt = document.createElement("div");
  txt.textContent = item.text || "(vacío)";
  div.append(meta, txt);
  if (item.out) {
    const out = document.createElement("div");
    out.className = "out";
    out.textContent = item.out;
    div.append(out);
  }
  list.prepend(div);
  while (list.children.length > 30) list.lastChild.remove();
}
function modeLabel() {
  return { type: "escrito", enter: "escrito + ⏎", clip: "portapapeles", cmd: "comando" }[mode] || mode;
}
function commandName(text) {
  return { type: "Escribir", enter: "Escribir + Intro", clip: "Portapapeles", cmd: "Comando" }[mode] || mode;
}

/* ---------------- WebSocket ---------------- */
function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/?pin=${encodeURIComponent(store.pin)}`;
}

function connect() {
  if (!store.pin) { showPin(); return; }
  setDot(null);
  try { if (ws) { ws.onclose = null; ws.close(); } } catch (e) {}
  ws = new WebSocket(wsUrl());
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    setDot(true);
    hidePin();
    setStatus(vad ? "manos libres activo" : "listo");
    fetchStatus();
  };
  whisperTimer();
  ws.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    handleMsg(m);
  };
  ws.onclose = () => {
    setDot(false);
    setStatus("reconectando…");
    setTimeout(() => { if (!ws || ws.readyState > 1) connect(); }, 2000);
  };
  ws.onerror = () => {};
}

function handleMsg(m) {
  switch (m.type) {
    case "hello":
      serverVersion = m.version || "";
      if (!m.whisper_ok) {
        addTranscript({ label: "aviso", text: "El servidor no tiene whisper-cli/modelo. Ejecuta install.sh en el PC.", err: true });
      }
      $("server-info").textContent = m.writer === "debug"
        ? "modo debug: no escribe en el PC (instala ydotool)"
        : `escritor: ${m.writer}`;
      break;
    case "state":
      if (m.value === "transcribing") { status = "transcribing"; setStatus("transcribiendo…", "transcribing"); }
      else if (m.value === "listening") { status = "listening"; setStatus("manos libres: escuchando"); }
      else if (m.value === "recording") { status = "recording"; setStatus("grabando…"); }
      else { status = "idle"; if (!vad) setStatus("listo"); }
      break;
    case "result":
      lastTranscript = m.text;
      if (m.text) {
        const label = commandName(m.mode || mode);
        addTranscript({ label, text: m.text, ok: true, out: m.output });
        if (m.cmd) addTranscript({ label: "resultado comando", text: m.cmd + (m.rc === 0 ? " ✓" : " ✗"), out: m.output });
      } else {
        addTranscript({ label: "resultado", text: "(sin voz reconocida)", err: true });
      }
      if (navigator.vibrate) navigator.vibrate(30);
      break;
    case "error":
      addTranscript({ label: "error", text: m.msg, err: true });
      break;
    case "info":
      setStatus(m.msg, "");
      break;
  }
}

let statusTimer = null;
function whisperTimer() { /* placeholder para futuro polling */ }

async function fetchStatus() {
  try {
    const r = await fetch(`/api/status?pin=${encodeURIComponent(store.pin)}`);
    if (r.status === 401) { showPin(); return; }
    const j = await r.json();
    const mb = j.model_mb ? `${Math.round(j.model_mb)}MB` : "sin modelo";
    $("server-info").textContent =
      `${j.hostname} · whisper ${j.whisper ? "✓" : "✗"} · ${j.model || "sin modelo"} ${j.model_ok ? "(" + mb + ")" : ""} · v${j.version || "?"}`;
  } catch (e) {}
}

/* ---------------- PIN ---------------- */
function showPin() {
  $("screen-pin").hidden = false;
  $("screen-app").hidden = true;
  $("pin").value = store.pin;
  $("pin").focus();
}
function hidePin() {
  $("screen-pin").hidden = true;
  $("screen-app").hidden = false;
}
async function tryConnect() {
  const p = $("pin").value.trim();
  if (p.length < 4) { pinError("El PIN tiene 6 dígitos (míralo en el PC)"); return; }
  $("btn-connect").disabled = true;
  $("btn-connect").textContent = "Conectando…";
  pinError(null);
  try {
    const r = await fetch(`/api/status?pin=${encodeURIComponent(p)}`);
    if (r.status === 401) { pinError("PIN incorrecto"); return; }
    store.pin = p;            // solo guardamos un PIN que el servidor validó
    hidePin();
    connect();
  } catch (e) {
    store.pin = p;            // fallo de red/SSL, no de PIN: lo guardamos igual
    pinError("No puedo alcanzar el servidor (misma WiFi, IP correcta y certificado aceptado)");
  } finally {
    $("btn-connect").disabled = false;
    $("btn-connect").textContent = "Conectar";
  }
}
function pinError(msg) {
  const el = $("pin-error");
  el.hidden = !msg;
  if (msg) el.textContent = msg;
}

/* ---------------- audio ---------------- */
async function initAudio() {
  if (audioCtx) return;
  if (!window.isSecureContext) {
    throw new Error("el micrófono exige HTTPS: abre la app con https:// y acepta el certificado");
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: true,
        sampleRate: 48000,
      },
    });
  } catch (e) {
    mediaStream = null;
    if (e && e.name === "NotAllowedError")
      throw new Error("permiso de micrófono denegado (actívalo en los ajustes del navegador)");
    if (e && e.name === "NotReadableError")
      throw new Error("el micrófono está ocupado o no disponible en este dispositivo");
    throw new Error("no pude abrir el micrófono (" + ((e && e.name) || e) + ")");
  }
  audioCtx = new AudioContext({ sampleRate: 48000 });
  await audioCtx.resume();
  await audioCtx.audioWorklet.addModule("/pcm-worklet.js");
  workletNode = new AudioWorkletNode(audioCtx, "deckmic-processor");
  workletNode.port.onmessage = (e) => {
    const { pcm, peak } = e.data;
    $("level").style.width = Math.min(100, Math.round(peak * 140)) + "%";
    if (ws && ws.readyState === 1 && (micOn || vad)) {
      ws.send(pcm);
    }
  };
  workletNode.connect(audioCtx.destination); // necesario en algunos navegadores
}

async function startSession(vadMode = false) {
  await initAudio();
  mode = store.mode;
  if (!ws || ws.readyState !== 1) {
    setStatus("sin conexión con el PC");
    return false;
  }
  ws.send(JSON.stringify({ type: "start", mode, vad: vadMode }));
  return true;
}

function endSession() {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "end" }));
}

function cancelSession() {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "cancel" }));
}

/* ---------------- botón push-to-talk ---------------- */
function micDown() {
  if (status === "transcribing") return;
  micOn = true;
  micHeldMs = Date.now();
  $("btn-mic").classList.add("rec");
  $("mic-lbl").innerHTML = "Grabando…<br>suelta para enviar";
  setStatus("grabando…");
  startSession(false)
    .then((ok) => { if (!ok) micUp(); })
    .catch((e) => micFail(e));
}
function micUp() {
  if (!micOn) return;
  micOn = false;
  $("btn-mic").classList.remove("rec");
  $("mic-lbl").innerHTML = "Mantén pulsado<br>para hablar";
  if (Date.now() - micHeldMs < 250) {
    cancelSession();
    setStatus("listo");
    return;
  }
  setStatus("enviando…");
  endSession();
}

function micFail(err) {
  micOn = false;
  $("btn-mic").classList.remove("rec");
  $("mic-lbl").innerHTML = "Mantén pulsado<br>para hablar";
  setStatus((err && err.message) || String(err), "err");
}

/* ---------------- VAD manos libres ---------------- */
async function toggleVad() {
  vad = $("vad").checked;
  if (vad) {
    try {
      const ok = await startSession(true);
      if (!ok) { $("vad").checked = false; vad = false; }
      else setStatus("manos libres: escuchando");
    } catch (e) {
      $("vad").checked = false;
      vad = false;
      setStatus((e && e.message) || String(e), "err");
    }
  } else {
    cancelSession();
    setStatus("listo");
  }
}

/* ---------------- wake lock ---------------- */
async function keepAwake(on) {
  try {
    if (on && "wakeLock" in navigator) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => {});
    } else if (wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch (e) {}
}

/* ---------------- eventos UI ---------------- */
document.addEventListener("DOMContentLoaded", () => {
  // PIN
  $("btn-connect").addEventListener("click", tryConnect);
  $("pin").addEventListener("keydown", (e) => { if (e.key === "Enter") tryConnect(); });

  // conectar al cargar si hay PIN guardado
  if (qs.get("pin")) store.pin = qs.get("pin");
  if (store.pin && qs.get("auto") !== "0") connect();
  else showPin();

  // restaurar ajustes guardados y re-aplicar el wake lock al arrancar
  $("cfg-space-key").checked = store.space;
  $("cfg-keep-awake").checked = store.keepAwake;
  keepAwake(store.keepAwake);

  // botón mic (touch + mouse)
  const micBtn = $("btn-mic");
  micBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); micDown(); });
  micBtn.addEventListener("pointerup", (e) => { e.preventDefault(); micUp(); });
  micBtn.addEventListener("pointerleave", () => micUp());
  micBtn.addEventListener("contextmenu", (e) => e.preventDefault());

  // barra espaciadora
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && $("cfg-space-key").checked && !e.repeat) {
      e.preventDefault(); micDown();
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space" && $("cfg-space-key").checked) { e.preventDefault(); micUp(); }
  });

  // modos
  document.querySelectorAll(".chip").forEach((c) => {
    if (c.dataset.mode === mode) c.classList.add("active");
    c.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      mode = c.dataset.mode;
      store.mode = mode;
    });
  });

  // VAD
  $("vad").addEventListener("change", toggleVad);

  // transcripciones
  $("btn-copy-last").addEventListener("click", async () => {
    if (!lastTranscript) return;
    try { await navigator.clipboard.writeText(lastTranscript); setStatus("copiado ✓"); }
    catch (e) { setStatus("no pude copiar"); }
  });
  $("btn-clear").addEventListener("click", () => { $("tr-list").innerHTML = ""; });

  // ajustes
  $("btn-config").addEventListener("click", () => {
    $("cfg-pin").value = store.pin;
    $("cfg-server").textContent = location.host + (serverVersion ? ` · servidor v${serverVersion}` : "");
    $("config-panel").hidden = false;
  });
  $("btn-cfg-close").addEventListener("click", () => { $("config-panel").hidden = true; });
  $("btn-cfg-save").addEventListener("click", () => {
    store.pin = $("cfg-pin").value.trim();
    store.space = $("cfg-space-key").checked;
    store.keepAwake = $("cfg-keep-awake").checked;
    keepAwake(store.keepAwake);
    $("config-panel").hidden = true;
    connect();
  });

  // visibilidad: cortar mic si la app pasa a segundo plano
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && micOn) micUp();
    if (document.hidden && vad) { $("vad").checked = false; toggleVad(); }
  });
});

/* ---------------- PWA ---------------- */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
