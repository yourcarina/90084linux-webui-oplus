#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FH="${FH_BIN:-$ROOT/fh_loader}"   # 默认用原始 19.06 构建（自建版 read 路径仍在修，见 third_party/README）
export FH_MODETYPE="${FH_MODETYPE:-0}"
export FH_ENABLEFLASH="${FH_ENABLEFLASH:-1}"
cd "$ROOT"
PORT="${1:-$(ls /dev/ttyUSB* 2>/dev/null | head -1)}"
FW="${FW:-/tmp/fw2}"
PROG="${PROG:-$FW/OPPO_SM8650_V1.0.21.melf}"
DIG="${DIG:-$FW/OPPO_SM8650_Digest.elf}"
SIG="${SIG:-$FW/OPPO_SM8650_Sign.bin}"
OUT="${OUT:-/tmp/fh_auth_$(date +%H%M%S)}"; mkdir -p "$OUT"

[ -n "$PORT" ] || { echo "没有 /dev/ttyUSB*，先把设备进 9008"; exit 1; }
echo "[1/5] Sahara → $PORT"
./QSaharaServer -p "$PORT" -v 1 -s "13:$PROG" > "$OUT/sahara.log" 2>&1 || true
grep -q "Successfully uploaded" "$OUT/sahara.log" || { echo "  Sahara 失败（脏状态？需要冷启动）"; tail -3 "$OUT/sahara.log"; exit 2; }
echo "  ok"

printf '<?xml version="1.0"?><data><verify value="ping" EnableVip="1"/></data>' > "$OUT/v.xml"
printf '<?xml version="1.0"?><data><sha256init Verbose="1"/></data>' > "$OUT/s.xml"

echo "[2/5] digest（--memoryname=ufs 必带）"
( cd "$OUT" && "$FH" --port="$PORT" --memoryname=ufs --search_path="$FW" --signeddigests="$(basename "$DIG")" --testvipimpact --noprompt > d.log 2>&1 ) ; echo "  rc=$?"
echo "[3/5] verify ping"
( cd "$OUT" && "$FH" --port="$PORT" --memoryname=ufs --sendxml="$OUT/v.xml" --noprompt > v.log 2>&1 ) ; echo "  rc=$?"
echo "[4/5] Sign.bin"
( cd "$OUT" && "$FH" --port="$PORT" --memoryname=ufs --search_path="$FW" --signeddigests="$(basename "$SIG")" --testvipimpact --noprompt > s.log 2>&1 ) ; echo "  rc=$?"
echo "[5/5] sha256init"
( cd "$OUT" && "$FH" --port="$PORT" --memoryname=ufs --sendxml="$OUT/s.xml" --noprompt > sh.log 2>&1 ) ; echo "  rc=$?"
echo "授权链完成（日志在 $OUT）"
