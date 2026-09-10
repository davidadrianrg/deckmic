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

let wsEverOpened = false;   // el WS llegó a abrirse en este intento

function connect() {
  if (!store.pin) { showPin(); return; }
  setDot(null);
  try { if (ws) { ws.onclose = null; ws.close(); } } catch (e) {}
  ws = new WebSocket(wsUrl());
  ws.binaryType = "arraybuffer";
  wsEverOpened = false;
  ws.onopen = () => {
    wsEverOpened = true;
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
  ws.onclose = async () => {
    setDot(false);
    // handshake rechazado sin llegar a abrir: distinguir PIN malo de red caída
    if (!wsEverOpened && store.pin) {
      const v = await validatePin(store.pin);
      if (v === "bad") {
        setStatus("PIN incorrecto", "err");
        store.pin = "";                 // no dejar guardado un PIN que el server rechaza
        showPin("El PIN guardado no vale: el servidor lo rechaza. Introdúcelo de nuevo.");
        return;                         // fuera del bucle de reintento
      }
    }
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
function showPin(msg) {
  $("screen-pin").hidden = false;
  $("screen-app").hidden = true;
  $("pin").value = store.pin;
  pinError(msg || null);
  $("pin").focus();
}
function hidePin() {
  $("screen-pin").hidden = true;
  $("screen-app").hidden = false;
}
/* Valida un PIN contra /api/status. Devuelve "ok" | "bad" | "unreachable". */
async function validatePin(pin) {
  try {
    // AbortSignal.timeout: un fetch colgado no puede congelar el panel de Ajustes
    const opts = ("timeout" in AbortSignal) ? { signal: AbortSignal.timeout(4000) } : {};
    const r = await fetch(`/api/status?pin=${encodeURIComponent(pin)}`, opts);
    return r.status === 401 ? "bad" : "ok";
  } catch (e) {
    return "unreachable";
  }
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
function cfgError(msg) {
  const el = $("cfg-error");
  el.hidden = !msg;
  if (msg) el.textContent = msg;
}

/* ---------------- audio ---------------- */
let audioRelaxed = false;   // reintento con {audio:true} si el micrófono entrega silencio

async function teardownAudio() {
  try { if (workletNode) workletNode.disconnect(); } catch (e) {}
  try { if (audioCtx) await audioCtx.close(); } catch (e) {}
  try { if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop()); } catch (e) {}
  audioCtx = null; workletNode = null; mediaStream = null;
}

async function initAudio() {
  if (audioCtx) return;
  if (!window.isSecureContext) {
    throw new Error("el micrófono exige HTTPS: abre la app con https:// y acepta el certificado");
  }
  // en algunos Android el juego de constraints ideal entrega silencio: si ya
  // reintentamos, usamos la captura del sistema sin restricciones
  const constraints = audioRelaxed
    ? { audio: true }
    : { audio: {
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: true,
      } };
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
  } catch (e) {
    mediaStream = null;
    if (e && e.name === "NotAllowedError")
      throw new Error("permiso de micrófono denegado (actívalo en los ajustes del navegador)");
    if (e && e.name === "NotReadableError")
      throw new Error("el micrófono está ocupado o no disponible en este dispositivo");
    throw new Error("no pude abrir el micrófono (" + ((e && e.name) || e) + ")");
  }
  // tasa nativa del dispositivo: el worklet remuestrea a 16 kHz el solo
  audioCtx = new AudioContext();
  await audioCtx.resume();
  if (audioCtx.state !== "running")
    throw new Error("audio bloqueado (" + audioCtx.state + "): abre la app en el navegador y prueba ahí");
  await audioCtx.audioWorklet.addModule("/pcm-worklet.js");
  workletNode = new AudioWorkletNode(audioCtx, "deckmic-processor");
  let cbCount = 0, maxPeak = 0;
  workletNode.port.onmessage = (e) => {
    const { pcm, peak } = e.data;
    if (cbCount < 1000) { cbCount++; if (peak > maxPeak) maxPeak = peak; }
    $("level").style.width = Math.min(100, Math.round(peak * 140)) + "%";
    if (ws && ws.readyState === 1 && (micOn || vad)) {
      ws.send(pcm);
    }
  };
  // el micro debe ENTRAR en el worklet: sin esto la captura va a ninguna parte
  const micSource = audioCtx.createMediaStreamSource(mediaStream);
  micSource.connect(workletNode);
  // el worklet saca silencio (no escribe outputs): mantener el grafo conectado
  // a destination hace que Chrome lo procese, sin realimentar el altavoz
  workletNode.connect(audioCtx.destination);
  // vigilante: cientos de callbacks con pico exactamente 0 = captura muda;
  // reabrir el micro sin restricciones (una sola vez)
  setTimeout(async () => {
    if (!audioCtx || audioRelaxed) return;
    if (cbCount >= 150 && maxPeak === 0) {
      audioRelaxed = true;
      await teardownAudio();
      try {
        await initAudio();
        setStatus("micro reconfigurado sin restricciones; vuelve a hablar", "");
      } catch (e) {
        micFail(e);
      }
    }
  }, 2500);
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
  $("btn-cfg-save").addEventListener("click", async () => {
    const btn = $("btn-cfg-save");
    const newPin = $("cfg-pin").value.trim();
    cfgError(null);
    btn.disabled = true;
    const restore = () => { btn.disabled = false; btn.textContent = "Guardar"; };
    try {
      if (newPin !== store.pin && newPin) {
        // validar antes de guardar y cerrar: así el usuario ve el error en el panel
        btn.textContent = "Comprobando…";
        const v = await validatePin(newPin);
        if (v === "bad") { cfgError("Ese PIN no lo acepta el servidor. Revísalo (lo ves con --check)."); return; }
        if (v === "unreachable") { cfgError("No puedo validar el PIN: no alcanzo el servidor (¿IP/certificado?)."); return; }
        store.pin = newPin;
      } else if (!newPin) {
        cfgError("El PIN no puede quedar vacío.");
        return;
      }
      store.space = $("cfg-space-key").checked;
      store.keepAwake = $("cfg-keep-awake").checked;
      keepAwake(store.keepAwake);
      $("config-panel").hidden = true;
      connect();
    } catch (e) {
      cfgError("Error al guardar: " + (e && e.message ? e.message : e));
    } finally {
      restore();
    }
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
