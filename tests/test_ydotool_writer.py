#!/usr/bin/env python3
"""Test rápido: YdotoolWriter llama a ydotool type -f - con el texto en stdin."""
import json, os, subprocess, sys, tempfile, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ydotool falso: loguea argv + stdin como JSON
LOG = tempfile.mktemp(prefix="ydotool-fake-")
FAKE = tempfile.mktemp(prefix="ydotool-bin-")
with open(FAKE, "w") as f:
    f.write(f"""#!/bin/sh
printf '%s::%s\\n' "$*" "$(cat)" >> {LOG}
""")
os.chmod(FAKE, 0o755)

os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"
from server import YdotoolWriter, ascii_sanitize

w = YdotoolWriter(FAKE)

# 1) texto ASCII simple
w.type_text("hola mundo")
# 2) texto con backslash (antes el escape lo comería)
w.type_text(r"C:\\ruta\\archivos")
# 3) español con acentos (antes: bytes UTF-8 fuera de tabla)
w.type_text("¿Cómo estás? señor Ñuñez")

with open(LOG) as f:
    lines = [l.rstrip("\n") for l in f]

for l in lines:
    argv, _, stdin = l.partition("::")
    print(f"argv={argv!r:45} stdin={stdin!r}")

print("sanitize:", repr(ascii_sanitize("¿Cómo estás? ñÑ áéíóü 🎙️")))
assert lines[0] == "type -f -::hola mundo", lines[0]
assert lines[1] == "type -f -::C:\\\\ruta\\\\archivos", lines[1]
assert lines[2] == "type -f -::Como estas? senor Nunez", lines[2]
assert "--key-duration" not in "".join(lines)
print("OK: sin --key-duration, texto por stdin intacto, acentos sanitizados")
os.unlink(FAKE); os.unlink(LOG)
