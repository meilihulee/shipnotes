#!/usr/bin/env bash
# Install ShipNotes on a fresh Ubuntu 22.04+ x86_64 box (run as root).
# Downloads llama.cpp (CPU build) and Gemma 3 1B Q4_K_M (~800 MB).
set -euo pipefail
LLAMA_BUILD="${LLAMA_BUILD:-b11716}"
PREFIX=/opt/shipnotes
SRC="$(cd "$(dirname "$0")/.." && pwd)"

id shipnotes >/dev/null 2>&1 || useradd --system --home "$PREFIX" --shell /usr/sbin/nologin shipnotes
mkdir -p "$PREFIX"/{bin,models,app}

if [ ! -x "$PREFIX/bin/llama-server" ]; then
  curl -fsSL -o /tmp/llama.tgz \
    "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_BUILD}/llama-${LLAMA_BUILD}-bin-ubuntu-x64.tar.gz"
  tar -xzf /tmp/llama.tgz -C /tmp
  cp -a /tmp/llama-${LLAMA_BUILD}/. "$PREFIX/bin/"
fi
if [ ! -f "$PREFIX/models/gemma-3-1b-it-Q4_K_M.gguf" ]; then
  curl -fL -o "$PREFIX/models/gemma-3-1b-it-Q4_K_M.gguf" \
    https://huggingface.co/ggml-org/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf
fi

if [ "$SRC" != "$PREFIX/app" ]; then cp -r "$SRC/shipnotes" "$SRC/static" "$PREFIX/app/"; fi
chown -R root:root "$PREFIX"
install -m 644 "$SRC/deploy/shipnotes-llm.service" /etc/systemd/system/
install -m 644 "$SRC/deploy/shipnotes.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now shipnotes-llm shipnotes
echo "ShipNotes is running on http://127.0.0.1:8411"
echo "For HTTPS, adapt deploy/nginx-shipnotes.conf and run certbot."
