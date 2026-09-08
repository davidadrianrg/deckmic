# 🎙️ DeckMic

**Tu móvil Android como micrófono inalámbrico + dictado Whisper para tu PC Linux / SteamOS.**

Habla desde el sofá → tu PC lo transcribe en local con Whisper (offline, sin nube) → el texto
se escribe en la aplicación que tengas enfocada. O en modo **Comando**: ejecuta órdenes de voz
en el PC.

```
┌──────────────┐   WSS/TLS    ┌───────────────────────────────┐
│   Móvil      │ ───────────► │  PC (SteamOS / Linux)         │
│  PWA (nave-  │  audio 16k   │  server.py (stdlib, 0 deps)   │
│  gador)      │  PCM binario │   ├─ VAD fin de frase         │
│  push-to-talk│ ◄─────────── │   ├─ whisper.cpp (local)      │
│  manos libres│  estado/JSON │   ├─ ydotool/xdotool → texto  │
└──────────────┘              │   └─ comandos de voz (shell)  │
                              └───────────────────────────────┘
```

## ¿Para qué?

- Dictar texto en **cualquier** aplicación con foco (navegador, editor, terminal, juego con
  teclado abierto…): escribe donde esté el cursor.
- Hablar con **agentes IA de terminal** (opencode, aide, claude-code…): modo *Escribir+Intro*
  envía el prompt y pulsa Enter — dictas y el agente responde, sin levantarte del sofá.
- **Comandos de voz** mapeados a shell: "abrir firefox", "volumen arriba"…
- 100% **local y privado**: ni el audio ni el texto salen de tu red WiFi.

## Requisitos

| Pieza | Motivo |
|---|---|
| PC Linux x86-64 (SteamOS 3 incluido) | servidor |
| Python ≥ 3.9 (preinstalado en SteamOS) | server.py, solo stdlib |
| `curl`, `openssl` | instalador / certificado |
| Móvil Android/iPhone con navegador | app web (PWA instalable) |
| Ambos en la **misma red WiFi** | WebSocket |

Opcionales pero recomendados en el PC: `ydotool` (Wayland/gamescope, escritura real),
`wl-clipboard` (modo portapapeles). Sin ellos funciona en modo *debug* (registra en
`data/typed.log`).

## Instalación (PC)

```bash
git clone https://github.com/davidadrianrg/deckmic.git
cd deckmic
./install.sh
```

El instalador:

1. Descarga `whisper-cli` (binario oficial de whisper.cpp) a `~/deckmic/bin/`.
2. Intenta instalar `ydotool` ( Wayland) o te guía si no hay paquete.
3. Descarga el modelo Whisper **multilingüe** (elige tamaño; el español va incluido).
4. Crea `~/deckmic/config.json` con un **PIN** aleatorio.
5. Ofrece servicio systemd de usuario (arranque automático).

En **SteamOS**: ejecútalo desde modo escritorio o por SSH. Todo se instala bajo `$HOME`
(~/deckmic), así que **sobrevive a las actualizaciones** del sistema (que borran /usr).

### Arrancar

```bash
python3 server.py --config ~/deckmic/config.json
# diagnóstico:  python3 server.py --check
```

Verás algo como:

```
14:02:11 INFO  DeckMic v0.1.0 escuchando en https://0.0.0.0:8443 (escritor: ydotool)
14:02:11 INFO    móvil → https://192.168.1.42:8443/  PIN: 482913
```

## Uso (móvil)

1. Misma WiFi que el PC. Abre `https://IP-DEL-PC:8443`
2. El certificado es autofirmado → aviso del navegador → **Opciones avanzadas → Continuar**.
3. Escribe el **PIN** → Conectar.
4. (Recomendado) Menú del navegador → **Añadir a pantalla de inicio** → se abre como app.
5. Elige modo y **mantén pulsado el micro** para hablar; suelta para transcribir.

### Modos

| Modo | Qué hace |
|---|---|
| ✍️ **Escribir** | Escribe el texto en la ventana enfocada del PC (donde está el cursor) |
| ⏎ **Escribir + Intro** | Igual + pulsa Enter (ideal para prompts a agentes IA: opencode, claude…) |
| 📋 **Portapapeles** | Copia el texto al portapapeles del PC (pégalo con Ctrl+V) |
| ⚡ **Comando** | Reconoce un alias de voz y ejecuta su comando shell |

El checkbox **Modo automático** activa manos libres: escucha continuo, corta al detectar
1.3 s de silencio, transcribe y sigue escuchando (VAD por energía).

## Comandos de voz

En `~/deckmic/config.json`:

```json
"commands": {
  "abrir firefox": "firefox",
  "volumen arriba": "pactl set-sink-volume @DEFAULT_SINK@ +10%",
  "captura pantalla": "spectacle"
}
```

Di **"abrir firefox"** con el modo ⚡ activo. El reconocimiento normaliza acentos y
mayúsculas ("ÁBRA FIREFOX" también vale).

## Cómo funciona por dentro

- **Servidor**: un único `server.py` en Python **solo-librería-estándar**. Implementa
  WebSocket (RFC 6455) a mano sobre `http.server` + TLS con cert autofirmado (necesario
  para `getUserMedia` desde el móvil). Cero dependencias pip.
- **Audio**: el navegador captura con `AudioWorklet`, remuestrea a PCM 16 kHz mono Int16 y
  envía binario por WS. El servidor detecta fin de frase por energía (VAD RMS configurable)
  y transcribe.
- **Whisper**: llama a `whisper-cli` (whisper.cpp) con el WAV temporal. Modelo por defecto
  `large-v3-turbo-q5_0` (547 MB, muy rápido en CPU y excelente en español).
- **Escritura**: `ydotool type` (funciona en Wayland y gamescope de SteamOS vía uinput)
  con fallback a `xdotool` (X11) o modo debug.
- **Privacidad**: todo en LAN. Sin cuentas, sin nube, sin telemetría.

## Configuración (config.json)

| Clave | Defecto | Descripción |
|---|---|---|
| `pin` | aleatorio | PIN de 6 dígitos para el móvil |
| `port` | `8443` | Puerto HTTPS/WSS |
| `lang` | `"auto"` | Forzar idioma: `"es"`, `"en"`… |
| `model` | large-v3-turbo-q5_0 | Ruta del modelo GGML |
| `writer` | `"auto"` | `ydotool`/`xdotool`/`debug`/`none` |
| `vad_threshold_db` | `-42` | Sensibilidad silencio (más bajo = más sensible) |
| `vad_silence_ms` | `1300` | ms de silencio para cortar frase |
| `commands` | `{}` | alias de voz → comando shell |

## Solución de problemas

- **No conecta**: mismo WiFi? Sin AP isolation? Puerto 8443 abierto?
  `python3 server.py --check` para diagnóstico.
- **No escribe en el PC**: ¿ydotool instalado y `ydotoold` corriendo con permisos uinput?
  En SteamOS: `sudo systemctl enable --now ydotool`. Verifica con `echo hola | ydotool type -` .
- **Certificado**: es autofirmado a propósito (LAN). Acéptalo una vez en el móvil.
- **Transcripción lenta**: usa `small-q5_1` o `tiny-q5_1`, o `beam_size: 1` en config.
- **Corta demasiado pronto / tarde**: ajusta `vad_silence_ms` y `vad_threshold_db`.

## Limitaciones (v0.1)

- La escritura con ydotool usa el layout "es" asumido; si usas otro layout, revisa
  `YDOTOOL_TYPE_LAYOUT` o cambia a modo portapapeles.
- No hay streaming parcial de transcripción (se transcribe al soltar / fin de frase).

## Licencia

MIT © David (davidadrianrg)
