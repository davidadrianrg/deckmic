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

Opcionales pero recomendados en el PC: `ydotool` (Wayland/gamescope, escritura real) y
`wl-clipboard` (modo portapapeles) — **el instalador compila ambos en un contenedor
podman rootless** si no están en el sistema. Sin ellos funciona en modo *debug*
(registra en `data/typed.log`).

**GPU opcional**: cualquier GPU con Vulkan (AMD RADV, Intel ANV) acelera Whisper
**~16×** (large-v3-turbo: 14,4 s → 0,9 s en una RX 7600). El instalador la detecta
y ofrece compilar `whisper-cli` con backend Vulkan en contenedor. No requiere
CUDA ni drivers de NVIDIA.

## Instalación (PC)

```bash
git clone https://github.com/davidadrianrg/deckmic.git
cd deckmic
./install.sh
```

El instalador:

1. Descarga `whisper-cli` (binario oficial de whisper.cpp) a `~/deckmic/bin/`.
2. Compila `ydotool`/`ydotoold` en un contenedor **podman rootless** (viene en
   SteamOS 3) → sin desactivar el readonly, sin root, sobrevive a updates.
   `ydotoold` se instala como **servicio de usuario**: `/dev/uinput` ya recibe
   permiso rw para el usuario del asiento (uaccess de systemd).
3. Descarga el modelo Whisper **multilingüe** (elige tamaño; el español va incluido).
4. Si detecta GPU con Vulkan, ofrece compilar `whisper-cli-vulkan`
   (ver [GPU (Vulkan)](#gpu-vulkan) más abajo).
5. Crea `~/deckmic/config.json` con un **PIN** aleatorio.
6. Ofrece servicio systemd de usuario (arranque automático).

En **SteamOS**: ejecútalo desde modo escritorio o por SSH. Todo se instala bajo `$HOME`
(~/deckmic), así que **sobrevive a las actualizaciones** del sistema (que borran /usr).

## GPU (Vulkan)

Con GPU (AMD RADV o Intel ANV) la transcripción pasa de ~14 s a **menos de 1 s**
con el mismo modelo `large-v3-turbo`:

| Binario | large-v3-turbo-q5_0 (3 s de audio) |
|---|---|
| `whisper-cli` (CPU, binario oficial) | 14.369 ms |
| `whisper-cli-vulkan` (GPU) | **887 ms** |

- El instalador la ofrece al detectar ICD de Vulkan + `/dev/dri/renderD*`;
  también puedes lanzarla a mano: `bash install.sh` (paso *[GPU]*).
- Se compila en un contenedor rootless con **Ubuntu 24.04** (glibc 2.39) para que
  el binario arranque en hosts con glibc igual o más nueva — SteamOS 3.x usa 2.41
  y `archlinux:latest` ya va por 2.43.
- Sale estático: solo necesita `libvulkan.so.1` y el driver del host (Mesa).
- El resultado vive en `~/deckmic/bin/whisper-cli-vulkan` y `config.json` lo usa
  automáticamente; el binario CPU queda como *fallback* (cambia `whisper_cli`
  en `config.json` para volver).

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
  "abrir opencode": "setsid konsole -e /home/deck/.opencode/bin/opencode >/dev/null 2>&1 &",
  "modo gaming": "if [[ \"$(steamosctl get-default-login-mode)\" == desktop ]]; then steamosctl switch-to-game-mode; else qdbus org.kde.Shutdown /Shutdown org.kde.Shutdown.logout; fi",
  "apagar el equipo": "systemctl poweroff",
  "reiniciar el equipo": "systemctl reboot",
  "volumen arriba": "pactl set-sink-volume @DEFAULT_SINK@ +10%",
  "captura pantalla": "spectacle"
}
```

Di **"abrir firefox"** con el modo ⚡ activo. El reconocimiento normaliza acentos y
mayúsculas ("ÁBRA FIREFOX" también vale) y admite errores de transcripción
(difflib). Combinado con el modo ⏎ **Escribir + Intro** puedes, por ejemplo,
decir *"abrir opencode"* y luego dictar los prompts directamente al agente.

En **SteamOS** también funcionan sin contraseña: *"modo gaming"* (usa la misma
orden que el icono *Return to Gaming Mode*: `steamosctl switch-to-game-mode` o
logout hacia gamescope) y *"apagar el equipo"* / *"reiniciar el equipo"*
(logind permite ambos a la sesión activa: `CanPowerOff`/`CanReboot` = yes).
Usa alias largos a propósito: el emparejamiento difuso con frases cortas
podría disparar apagados accidentales.

## Cómo funciona por dentro

- **Servidor**: un único `server.py` en Python **solo-librería-estándar**. Implementa
  WebSocket (RFC 6455) a mano sobre `http.server` + TLS con cert autofirmado (necesario
  para `getUserMedia` desde el móvil). Cero dependencias pip.
- **Audio**: el navegador captura con `AudioWorklet`, remuestrea a PCM 16 kHz mono Int16 y
  envía binario por WS. El servidor detecta fin de frase por energía (VAD RMS configurable)
  y transcribe.
- **Whisper**: llama a `whisper-cli` (whisper.cpp) con el WAV temporal. Modelo por defecto
  `large-v3-turbo-q5_0` (547 MB, excelente en español). Con GPU Vulkan opcional
  (~16× más rápido, ver sección GPU).
- **Escritura**: `ydotool type` (funciona en Wayland y gamescope de SteamOS vía uinput)
  con fallback a `xdotool` (X11) o modo debug.
- **Privacidad**: todo en LAN. Sin cuentas, sin nube, sin telemetría.

## Configuración (config.json)

| Clave | Defecto | Descripción |
|---|---|---|
| `pin` | aleatorio | PIN de 6 dígitos para el móvil |
| `port` | `8443` | Puerto HTTPS/WSS |
| `whisper_cli` | `~/deckmic/bin/whisper-cli` | Binario whisper.cpp (usa `…-vulkan` si lo compilaste) |
| `lang` | `"auto"` | Forzar idioma: `"es"`, `"en"`… |
| `model` | large-v3-turbo-q5_0 | Ruta del modelo GGML |
| `writer` | `"auto"` | `ydotool`/`xdotool`/`debug`/`none` |
| `vad_threshold_db` | `-42` | Sensibilidad silencio (más bajo = más sensible) |
| `vad_silence_ms` | `1300` | ms de silencio para cortar frase |
| `commands` | `{}` | alias de voz → comando shell |

## Solución de problemas

- **No conecta**: mismo WiFi? Sin AP isolation? Puerto 8443 abierto?
  `python3 server.py --check` para diagnóstico.
- **No escribe en el PC**: ¿está el daemon corriendo?
  `systemctl --user status ydotoold` (servicio de usuario, sin sudo).
  Verifica: `echo hola | ydotool type -f -`
- **Certificado**: es autofirmado a propósito (LAN). Acéptalo una vez en el móvil.
- **Transcripción lenta**: compila la versión GPU (`offer_whisper_gpu` del instalador,
  ver sección GPU); si no hay GPU, usa `small-q5_1` o `tiny-q5_1`, o `beam_size: 1`.
- **Corta demasiado pronto / tarde**: ajusta `vad_silence_ms` y `vad_threshold_db`.
- **La UI muestra versión vieja tras actualizar**: la caché del service worker se
  versiona con el servidor (`deckmic-vX.Y.Z`, visible en Ajustes); cierra y reabre la PWA.

## Limitaciones (v0.1)

- ydotool teclea vía keycodes ASCII: los acentos se escriben sin tilde
  ("cómo" → "como"). Para el texto exacto usa el modo 📋 Portapapeles.
- No hay streaming parcial de transcripción (se transcribe al soltar / fin de frase).

## Licencia

MIT © David (davidadrianrg)
