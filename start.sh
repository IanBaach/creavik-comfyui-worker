#!/bin/bash
set -e

CKPT_DIR=/comfyui/models/checkpoints
CKPT_FILE="$CKPT_DIR/sd_xl_base_1.0.safetensors"

if [ ! -f "$CKPT_FILE" ]; then
  echo "[start.sh] downloading SDXL base checkpoint (first boot, no network volume)..."
  mkdir -p "$CKPT_DIR"
  curl -L --fail -o "$CKPT_FILE" \
    "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors"
fi

echo "[start.sh] launching ComfyUI..."
python -u /comfyui/main.py --disable-auto-launch --disable-metadata --port 8188 &
echo $! > /tmp/comfyui.pid

echo "[start.sh] launching handler..."
python -u /handler.py
