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
    return 0
  fi

  # binarios precompilados de github (proyectos de comunidad)
  local urls=(
    "https://github.com/ReimuNotMoe/ydotool/releases/latest/download/ydotool.tar.gz"
  )
  for u in "${urls[@]}"; do
    say "  probando $u"
    if curl -fsSL "$u" -o /tmp/ydotool.tar.gz 2>/dev/null; then
      mkdir -p /tmp/ydotool-x
      if tar -xzf /tmp/ydotool.tar.gz -C /tmp/ydotool-x 2>/dev/null; then
        local found
        found="$(find /tmp/ydotool-x -type f \( -name 'ydotool' -o -name 'ydotoold' \) | head -2 || true)"
        if [[ -n "$found" ]]; then
          find /tmp/ydotool-x -type f -name 'ydotool' -exec install -m 755 {} "$INSTALL_DIR/bin/ydotool" \; 2>/dev/null || true
          find /tmp/ydotool-x -type f -name 'ydotoold' -exec install -m 755 {} "$INSTALL_DIR/bin/ydotoold" \; 2>/dev/null || true
          if [[ -x "$INSTALL_DIR/bin/ydotool" ]]; then
            ok "instalado $INSTALL_DIR/bin/ydotool"
            YDOTOOL_BIN="$INSTALL_DIR/bin/ydotool"
            rm -rf /tmp/ydotool-x /tmp/ydotool.tar.gz
            return 0
          fi
        fi
      fi
    fi
  done
  warn "no pude descargar ydotool precompilado."
  if [[ "$STEAMOS" -eq 1 ]]; then
    cat <<EOF

  En SteamOS (modo escritorio, terminal):
    steamos-readonly disable
    sudo pacman -S ydotool
    sudo systemctl enable --now ydotool    # daemon uinput
    (opcional) steamos-readonly enable

  Alternativa: compilar ydotool desde fuente
    git clone https://github.com/ReimuNotMoe/ydotool ~/ydotool-src
    cd ~/ydotool-src && mkdir build && cd build
    cmake .. && make -j\$(nproc) && sudo make install
EOF
  else
    echo "  Instala con tu gestor de paquetes: sudo pacman -S ydotool / sudo apt install ydotool"
  fi
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
    # asegurar rutas de binarios locales
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
After=network-online.target

[Service]
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
