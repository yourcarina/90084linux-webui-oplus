#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FH="${FH_BIN:-$ROOT/fh_loader}"   # 默认用原始 19.06 构建（自建版 read 路径仍在修，见 third_party/README）
export FH_MODETYPE="${FH_MODETYPE:-0}"
export FH_ENABLEFLASH="${FH_ENABLEFLASH:-1}"
cd "$ROOT"
LABEL=$1; LUN=$2; START=$3; TOT=$4; OUTIMG=$5; CHUNK=${6:-8192}
PORT="${PORT:-$(ls /dev/ttyUSB* 2>/dev/null | head -1)}"
WORK=$(mktemp -d /tmp/fhread.XXXXXX); : > "$OUTIMG"
CUR=$START; LEFT=$TOT; I=0; FAIL=0
while [ $LEFT -gt 0 ]; do
  N=$(( LEFT > CHUNK ? CHUNK : LEFT )); F=$(printf "c_%03d.bin" $I)
  printf '<?xml version="1.0" ?><data><configure MemoryName="ufs" ModeType="0" EnableFlash="1" /><read SECTOR_SIZE_IN_BYTES="4096" filename="%s" physical_partition_number="%s" start_sector="%s" num_partition_sectors="%s" label="%s"/></data>' "$F" "$LUN" "$CUR" "$N" "$LABEL" > "$WORK/r.xml"
  ok=0
  for t in 1 2 3; do
    ( cd "$WORK" && "$FH" --port="$PORT" --memoryname=ufs --sendxml=r.xml --noprompt --loglevel=0 >/dev/null 2>&1 )
    sz=$(stat -c%s "$WORK/$F" 2>/dev/null || echo 0)
    [ "$sz" = "$((N*4096))" ] && { ok=1; break; }; sleep 0.3
  done
  if [ $ok = 1 ]; then cat "$WORK/$F" >> "$OUTIMG"; else FAIL=$((FAIL+1)); echo "  缺口 lba=$CUR sectors=$N"; fi
  rm -f "$WORK/$F"; CUR=$((CUR+N)); LEFT=$((LEFT-N)); I=$((I+1))
done
rm -rf "$WORK"
echo "回读完成: $(stat -c%s "$OUTIMG") 字节, 缺口块=$FAIL"
sha256sum "$OUTIMG" | awk '{print "sha256:",$1}'
