#!/usr/bin/env bash
# DeckMic — instalador para Linux/SteamOS 3 (Holo).
# - Detecta SteamOS (read-only) y usa ~/deckmic (sobrevive a actualizaciones).
# - Descarga whisper-cli binario oficial (x86-64) o compila desde fuente.
# - Instala/configura ydotool + ydotoold (escritura en Wayland/gamescope).
# - Crea servicio systemd user (opcional) para arranque automático.
set -euo pipefail

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; RED=$'\033[31m'; NC=$'\033[0m'
say()  { printf "%s\n" "${BOLD}$*${NC}"; }
ok()   { printf "%s✔ %s${NC}\n" "$GREEN" "$*"; }
warn() { printf "%s⚠ %s${NC}\n" "$YEL" "$*"; }
err()  { printf "%s✘ %s${NC}\n" "$RED" "$*"; }

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/deckmic"
WHISPER_VERSION="b4938"
MODEL_DEFAULT="ggml-large-v3-turbo-q5_0.bin"
MODEL_URL_BASE="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

say "DeckMic — instalación"
echo "  carpeta app : $APP_DIR"
echo "  carpeta datos: $INSTALL_DIR"

# --------------------------------------------------------------------------
detect_os() {
  if grep -q steamos /etc/os-release 2>/dev/null; then
    STEAMOS=1
    say "SteamOS detectado (partición /usr solo-lectura)."
    echo "  - Todo vive en \$HOME (sobrevive a actualizaciones)."
    echo "  - Requiere Developer Mode + SSH (o Desktop Mode + terminal)."
  else
    STEAMOS=0
    say "Linux genérico detectado."
  fi
}

# --------------------------------------------------------------------------
install_whisper() {
  say "[1/4] whisper-cli"
  mkdir -p "$INSTALL_DIR/bin"
  local W="$INSTALL_DIR/bin/whisper-cli"

  if [[ -x "$W" ]]; then
    ok "ya instalado: $W"
    return 0
  fi

  # intento 1: binario oficial precompilado (Ubuntu x86-64, funciona en SteamOS)
  local URL="https://github.com/ggml-org/whisper.cpp/releases/download/${WHISPER_VERSION}/whisper-bin-ubuntu-x64.tar.gz"
  say "  descargando binario oficial ($WHISPER_VERSION)…"
  if curl -fsSL "$URL" -o /tmp/whisper-bin.tar.gz; then
    rm -rf /tmp/whisper-bin
    mkdir -p /tmp/whisper-bin
    tar -xzf /tmp/whisper-bin.tar.gz -C /tmp/whisper-bin
    local found
    found="$(find /tmp/whisper-bin -type f -name 'whisper-cli' | head -1 || true)"
    if [[ -n "$found" ]]; then
      # copiar DIRECTORIO completo: whisper-cli carga libggml*.so de su carpeta
      local SRC_DIR
      SRC_DIR="$(dirname "$found")"
      mkdir -p "$INSTALL_DIR/whisper"
      cp -a "$SRC_DIR"/. "$INSTALL_DIR/whisper/"
      chmod +x "$INSTALL_DIR/whisper/whisper-cli"
      ln -sf "$INSTALL_DIR/whisper/whisper-cli" "$W"
      ok "instalado $INSTALL_DIR/whisper/whisper-cli (+libs ggml)"
      rm -rf /tmp/whisper-bin /tmp/whisper-bin.tar.gz
      return 0
    fi
    warn "el tarball no contenía whisper-cli; compilo desde fuente"
  else
    warn "no pude descargar el binario; compilo desde fuente"
  fi

  # intento 2: compilar (requiere git, cmake, gcc)
  compile_whisper
}

compile_whisper() {
  say "  compilando whisper.cpp (necesita git+cmake+gcc)…"
  if ! command -v cmake >/dev/null || ! command -v gcc >/dev/null; then
    err "faltan cmake/gcc y no hay binario. Instálalos (en SteamOS: steamos-readonly disable + pacman -S cmake gcc) e reintenta."
    return 1
  fi
  local SRC=/tmp/whisper.cpp
  rm -rf "$SRC"
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$SRC"
  cmake -S "$SRC" -B "$SRC"/build -DGGML_NATIVE=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=OFF -DBUILD_SHARED_LIBS=OFF
  cmake --build "$SRC"/build --config Release -j"$(nproc)" --target whisper-cli
  install -m 755 "$SRC"/build/bin/whisper-cli "$INSTALL_DIR/bin/whisper-cli"
  ok "compilado e instalado en $INSTALL_DIR/bin/whisper-cli"
  rm -rf "$SRC"
}

# --------------------------------------------------------------------------
build_ydotool_container() {
  # Compila ydotool+ydotoold (master) dentro de un contenedor rootless:
  # - SteamOS 3 trae podman funcional sin root → no hace falta desactivar el
  #   readonly ni inicializar el keyring de pacman, y no se borra al actualizar.
  # - master (no v1.0.4) para tener el socket en $XDG_RUNTIME_DIR/.ydotool_socket
  #   y los defaults modernos de "type".
  # - El binario resultante solo depende de glibc → corre bien en el host.
  local RUNNER=""
  if command -v podman >/dev/null 2>&1; then RUNNER=podman
  elif command -v docker >/dev/null 2>&1; then RUNNER=docker
  else warn "ni podman ni docker disponibles"; return 1; fi

  say "  compilando ydotool (master) en contenedor $RUNNER (tarda unos minutos)…"
  mkdir -p "$INSTALL_DIR/bin"
  if ! "$RUNNER" run --rm -v "$INSTALL_DIR":/out archlinux:latest bash -c '
    set -e
    pacman -Sy --noconfirm --needed git cmake make gcc scdoc libevdev
    git clone --depth 1 https://github.com/ReimuNotMoe/ydotool /src
    # CMake >= 4 rechaza cmake_minimum_required antiguos sin esto:
    cmake -S /src -B /src/build -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5
    cmake --build /src/build -j"$(nproc)"
    find /src/build -type f \( -name ydotool -o -name ydotoold \) \
      -exec cp {} /out/bin/ \;
  '; then
    err "la compilación en contenedor falló"
    return 1
  fi
  [[ -f "$INSTALL_DIR/bin/ydotool"  ]] && chmod 755 "$INSTALL_DIR/bin/ydotool"
  [[ -f "$INSTALL_DIR/bin/ydotoold" ]] && chmod 755 "$INSTALL_DIR/bin/ydotoold"
  [[ -x "$INSTALL_DIR/bin/ydotool" ]]
}

setup_ydotoold_user_service() {
  # Daemon como servicio de USUARIO: /dev/uinput recibe ACL rw para el usuario
  # del asiento (uaccess de systemd), así que no hace falta root.
  [[ -x "$INSTALL_DIR/bin/ydotoold" ]] || return 1
  local UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/ydotoold.service" <<EOF
[Unit]
Description=ydotoold — daemon uinput para ydotool (DeckMic)

[Service]
# Nota: ydotoold NO lee YDOTOOL_SOCKET; escucha siempre en
# $XDG_RUNTIME_DIR/.ydotool_socket (systemd user lo define).
ExecStart=$INSTALL_DIR/bin/ydotoold
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now ydotoold.service 2>/dev/null \
    || warn "no pude arrancar ydotoold ahora; en modo escritorio: systemctl --user start ydotoold"
  ok "ydotoold corriendo como servicio de usuario (socket: \$XDG_RUNTIME_DIR/.ydotool_socket)"
}

install_ydotool() {
  say "[2/4] ydotool (escritura en Wayland/gamescope)"
  if command -v ydotool >/dev/null 2>&1; then
    ok "ya en PATH: $(command -v ydotool)"
    YDOTOOL_BIN="$(command -v ydotool)"
    return 0
  fi
  if [[ -x "$INSTALL_DIR/bin/ydotool" ]]; then
    ok "ya instalado: $INSTALL_DIR/bin/ydotool"
    YDOTOOL_BIN="$INSTALL_DIR/bin/ydotool"
    setup_ydotoold_user_service || true
    return 0
  fi

  if build_ydotool_container; then
    ok "instalado $INSTALL_DIR/bin/ydotool"
    [[ -x "$INSTALL_DIR/bin/ydotoold" ]] \
      || warn "ydotoold no se compiló: no habrá servicio de usuario"
    YDOTOOL_BIN="$INSTALL_DIR/bin/ydotool"
    setup_ydotoold_user_service || true
    return 0
  fi

  warn "no pude instalar ydotool automáticamente."
  cat <<EOF

  Opciones manuales:
  - SteamOS 3: usa podman (viene de serie). Activa Developer Mode si hace falta.
  - Cualquier Linux: sudo pacman -S ydotool  /  sudo apt install ydotool
    (en SteamOS eso exige 'steamos-readonly disable' + 'pacman-key --init/--populate'
     y se borra con cada actualización — por eso preferimos el contenedor)
EOF
  return 0  # no bloquear: el servidor usará modo debug
}

# --------------------------------------------------------------------------
download_model() {
  say "[3/4] modelo Whisper (offline, en tu PC)"
  local MODEL_DIR="$INSTALL_DIR/models"
  mkdir -p "$MODEL_DIR"
  local model="${1:-$MODEL_DEFAULT}"
  local dest="$MODEL_DIR/$model"
  cfg_model_path="$dest"

  if [[ -f "$dest" ]]; then
    ok "ya descargado: $model"
    return 0
  fi
  cat <<EOF

  Elige modelo (español incluido en todos los multilingües):
    1) large-v3-turbo-q5_0  547MB  — máxima calidad (recomendado, GPU no necesaria)
    2) small-q5_1           181MB  — rápido, calidad media
    3) tiny-q5_1            57MB   — ultra rápido, calidad baja
    4) saltar descarga

EOF
  read -r -p "  Modelo [1]: " choice
  case "${choice:-1}" in
    1) model="ggml-large-v3-turbo-q5_0.bin" ;;
    2) model="ggml-small-q5_1.bin" ;;
    3) model="ggml-tiny-q5_1.bin" ;;
    4) warn "sin modelo; edítalo luego en $INSTALL_DIR/config.json"; return 0 ;;
  esac
  dest="$MODEL_DIR/$model"
  cfg_model_path="$dest"
  if [[ -f "$dest" ]]; then ok "ya descargado: $model"; return 0; fi
  say "  descargando $model (~$(du -h /dev/null 2>/dev/null | cut -f1))…"
  curl -fL --progress-bar "$MODEL_URL_BASE/$model" -o "$dest.part"
  mv "$dest.part" "$dest"
  ok "modelo en $dest"
}

# --------------------------------------------------------------------------
write_config() {
  say "[4/4] configuración"
  local CFG="$INSTALL_DIR/config.json"
  local PIN
  PIN="$(shuf -i 100000-999999 -n 1)"
  if [[ -f "$CFG" ]]; then
    ok "config ya existe: $CFG (no lo toco)"
    # asegurar ruta del ydotool recién instalado (el modo "auto" de server.py
    # solo detecta rutas absolutas existentes; "~" no pasa os.path.isfile)
    if [[ -n "${YDOTOOL_BIN:-}" ]]; then
      python3 - "$CFG" "$YDOTOOL_BIN" <<'PYEOF'
import json, sys
cfg_path, yd = sys.argv[1], sys.argv[2]
try:
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("ydotool") != yd:
        cfg["ydotool"] = yd
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"      ydotool → {yd} (config actualizado)")
except Exception as e:
    print(f"      aviso: no pude actualizar 'ydotool' en config ({e})")
PYEOF
    fi
    return 0
  fi
  local model_rel="models/$(basename "${cfg_model_path:-$MODEL_DEFAULT}")"
  local whisper_bin="$INSTALL_DIR/bin/whisper-cli"
  local yd="${YDOTOOL_BIN:-}"
  cat > "$CFG" <<EOF
{
  "pin": "$PIN",
  "port": 8443,
  "tls": true,
  "whisper_cli": "$whisper_bin",
  "model": "$INSTALL_DIR/$model_rel",
  "lang": "auto",
  "beam_size": 5,
  "writer": "auto",
  "ydotool": "${yd:-ydotool}",
  "vad_threshold_db": -42.0,
  "vad_silence_ms": 1300,
  "commands": {
    "abrir firefox": "firefox",
    "abrir terminal": "kgx",
    "volumen arriba": "pactl set-sink-volume @DEFAULT_SINK@ +10%",
    "volumen abajo": "pactl set-sink-volume @DEFAULT_SINK@ -10%"
  }
}
EOF
  chmod 600 "$CFG"
  ok "config creado: $CFG"
  echo "      ${BOLD}PIN: $PIN${NC}"
}

# --------------------------------------------------------------------------
offer_service() {
  echo
  if [[ ! -d "$HOME/.config/systemd/user" && "$STEAMOS" -eq 1 ]]; then
    warn "SteamOS: los servicios user de systemd arrancan tras iniciar sesión en modo escritorio."
  fi
  read -r -p "¿Crear servicio systemd para arranque automático? [s/N]: " ans
  [[ "${ans,,}" == "s" ]] || { say "ok, arranca manualmente: python3 $APP_DIR/server.py --config $HOME/deckmic/config.json"; return 0; }
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/deckmic.service" <<EOF
[Unit]
Description=DeckMic — móvil como micrófono/dictado
After=network-online.target ydotoold.service

[Service]
Environment=YDOTOOL_SOCKET=%t/.ydotool_socket
ExecStart=/usr/bin/env python3 $APP_DIR/server.py --config $HOME/deckmic/config.json
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now deckmic.service 2>/dev/null || warn "no pude activar el servicio ahora (arráncalo en modo escritorio)"
  ok "servicio deckmic activado (systemctl --user status deckmic)"
}

# --------------------------------------------------------------------------
main() {
  detect_os
  install_whisper
  install_ydotool
  download_model "${MODEL:-}"
  write_config
  offer_service
  echo
  say "Listo. En el PC:"
  echo "    python3 $APP_DIR/server.py --config $HOME/deckmic/config.json"
  echo "  (o systemctl --user start deckmic)"
  echo
  say "En el móvil (misma WiFi):"
  echo "    1. Abre https://IP-DEL-PC:8443  (acepta el aviso del certificado)"
  echo "    2. Introduce el PIN"
  echo "    3. Menú ⋮ del navegador → 'Añadir a pantalla de inicio' (PWA)"
  echo
}

main "$@"
