#!/usr/bin/env bash
# 9008 握手：端口出现 → 直接交给 QSaharaServer
set -u
cd "$(dirname "$0")/.."
ROOT="$PWD"
OUT="${OUT:-data/handshake-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
FW="${FW:-SM8650_8Gen3}"
MELF="${MELF:-$FW/OPPO_SM8650_V1.0.21.melf}"
DIG="${DIG:-$FW/OPPO_SM8650_Digest.elf}"
SIG="${SIG:-$FW/OPPO_SM8650_Sign.bin}"
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/run.log"; }

say "等待 /dev/ttyUSB* 出现（最长 600s）…"
P=""
for _ in $(seq 1 12000); do
  for d in /dev/ttyUSB*; do [ -e "$d" ] && { P="$d"; break; }; done
  [ -n "$P" ] && break
  sleep 0.05
done
[ -n "$P" ] || { say "超时：没有串口"; exit 1; }
say "端口：$P"

say "[1/5] Sahara"
timeout 90 ./QSaharaServer -p "$P" -v 1 -s "13:$MELF" > "$OUT/1-sahara.log" 2>&1
grep -q "Sahara protocol completed" "$OUT/1-sahara.log" || { say "✗ Sahara 失败，见 $OUT/1-sahara.log"; exit 2; }
say "  ✓ Sahara OK"

say "[2/5] digest"
timeout 60 ./fh_loader --port="$P" --memoryname=ufs --signeddigests="$DIG" \
    --search_path="$FW" --testvipimpact --noprompt > "$OUT/2-digest.log" 2>&1
say "  rc=$?"

mkdir -p "$OUT/x"
printf '<?xml version="1.0"?><data><verify value="ping" EnableVip="1"/></data>' > "$OUT/x/verify.xml"
say "[3/5] verify ping"
( cd "$OUT/x" && timeout 45 "$ROOT/fh_loader" --port="$P" --memoryname=ufs \
    --sendxml=verify.xml --noprompt > "$OUT/3-verify.log" 2>&1 ); say "  rc=$?"

say "[4/5] Sign.bin"
timeout 60 ./fh_loader --port="$P" --memoryname=ufs --signeddigests="$SIG" \
    --search_path="$FW" --testvipimpact --noprompt > "$OUT/4-sign.log" 2>&1
say "  rc=$?"

printf '<?xml version="1.0"?><data><sha256init Verbose="1"/></data>' > "$OUT/x/sha.xml"
say "[5/5] sha256init"
( cd "$OUT/x" && timeout 45 "$ROOT/fh_loader" --port="$P" --memoryname=ufs \
    --sendxml=sha.xml --noprompt > "$OUT/5-sha.log" 2>&1 ); say "  rc=$?"

say "=== 完成，日志在 $OUT ==="
