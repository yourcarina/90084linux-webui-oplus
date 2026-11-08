#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FH="${FH_BIN:-$ROOT/fh_loader}"   # 默认用原始 19.06 构建（自建版 read 路径仍在修，见 third_party/README）
export FH_MODETYPE="${FH_MODETYPE:-0}"
export FH_ENABLEFLASH="${FH_ENABLEFLASH:-1}"
cd "$ROOT"
LABEL=$1; LUN=$2; START=$3; TOT=$4; IMG=$5; CHUNK=${6:-8192}
PORT="${PORT:-$(ls /dev/ttyUSB* 2>/dev/null | head -1)}"
WORK=$(mktemp -d /tmp/fhwrite.XXXXXX)
[ "$(stat -c%s "$IMG")" = "$((TOT*4096))" ] || { echo "镜像大小与 sectors*4096 不一致"; exit 1; }
CUR=$START; LEFT=$TOT; I=0; FAIL=0
while [ $LEFT -gt 0 ]; do
  N=$(( LEFT > CHUNK ? CHUNK : LEFT ))
  dd if="$IMG" of="$WORK/w_$I.bin" bs=4096 skip=$(( CUR - START )) count=$N status=none
  printf '<?xml version="1.0" ?><data><program SECTOR_SIZE_IN_BYTES="4096" filename="w_%s.bin" physical_partition_number="%s" start_sector="%s" num_partition_sectors="%s" label="%s"/></data>' "$I" "$LUN" "$CUR" "$N" "$LABEL" > "$WORK/w_$I.xml"
  ( cd "$WORK" && timeout 180 "$FH" --port="$PORT" --memoryname=ufs --search_path="$WORK" --sendxml="w_$I.xml" --noprompt --showpercentagecomplete > "w_$I.log" 2>&1 )
  rc=$?
  echo "  chunk $I lba=$CUR sectors=$N rc=$rc $(grep -oE "TARGET SAID: '[^']*" "$WORK/w_$I.log" | sed "s/TARGET SAID: '//" | grep -E "ERROR|permission|Finished" | head -1)"
  [ $rc -ne 0 ] && FAIL=$((FAIL+1))
  rm -f "$WORK/w_$I.bin"; CUR=$((CUR+N)); LEFT=$((LEFT-N)); I=$((I+1))
done
rm -rf "$WORK"
echo "写入完成: 失败块=$FAIL"
