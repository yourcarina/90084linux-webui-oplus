#!/usr/bin/env bash
# 从 third_party/fh_loader-src 构建带 OPPO 扩展的 fh_loader
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=third_party/fh_loader-src
OUT=${1:-build/fh_loader_oppo}
mkdir -p "$(dirname "$OUT")"
echo "[build] $SRC → $OUT"
g++ -w -fpermissive -O2 -D_FILE_OFFSET_BITS=64 \
    "$SRC/fh_loader.cpp" "$SRC/fh_loader_sha.cpp" \
    -o "$OUT" -lpthread
echo "[build] ok: $(stat -c%s "$OUT") bytes"
"$OUT" --port=/dev/null --noprompt 2>&1 | head -4 || true
