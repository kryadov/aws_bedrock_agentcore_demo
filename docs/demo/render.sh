#!/usr/bin/env sh
# Render docs/demo/chat.gif from docs/demo/session.json (offline, no AWS): vhs records the terminal
# frames, ffmpeg joins them. Two steps because vhs on Windows exits 0 without writing a GIF.
# FPS = frames per second of the GIF (vhs captures at 50).
set -e
cd "$(dirname "$0")/../.."
unset VIRTUAL_ENV
FRAMES=docs/demo/frames
rm -rf "$FRAMES"
vhs docs/demo/chat.tape
ffmpeg -y -loglevel error -framerate 50 -i "$FRAMES/frame-text-%05d.png" -framerate 50 -i "$FRAMES/frame-cursor-%05d.png" \
  -filter_complex "[0][1]overlay,pad=iw+40:ih+40:20:20:color=0x1e1e2e,fps=${FPS:-25},split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=none" \
  docs/demo/chat.gif
rm -rf "$FRAMES"
ls -l docs/demo/chat.gif
