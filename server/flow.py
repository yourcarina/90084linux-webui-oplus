"""统一任务编排：VIP 授权链 + 分区提取/写入。

只保留一套授权方式（VIP）：
  Sahara 上传 programmer → digest → verify ping → Sign.bin → sha256init
之后按分区逐个执行 <read>（提取）或 <program>（写入）。
"""
from __future__ import annotations

import math
import shlex
from pathlib import Path

from . import config as C

CONFIG_PREFIX = ('<configure MemoryName="ufs" ModeType="0" EnableFlash="1" '
                 'MaxPayloadSizeToTargetInBytes="1048576" />')
VERIFY_XML = '<?xml version="1.0"?><data><verify value="ping" EnableVip="1"/></data>'
SHA_XML = '<?xml version="1.0"?><data><sha256init Verbose="1"/></data>'


def _preview(argv: list[str]) -> str:
    out = []
    for a in argv:
        out.append("'" + a.replace("'", "'\\''") + "'" if any(c in a for c in " \t\"'$") else a)
    return " ".join(out)


def _human(n: int) -> str:
    v = float(n)
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or u == "TiB":
            return f"{v:.1f} {u}"
        v /= 1024
    return f"{n} B"


def build_job(spec: dict) -> dict:
    warnings: list[dict] = []
    steps: list[dict] = []

    programmer = str(spec.get("programmer") or "").strip()
    digest = str(spec.get("digest") or "").strip()
    sign = str(spec.get("sign") or "").strip()
    phase = str(spec.get("phase") or spec.get("mode") or "read").lower()
    mode = "read" if phase == "firehose" else phase
    parts = [] if phase == "firehose" else (spec.get("parts") or [])
    chunk = int(spec.get("chunkSectors") or 8192)
    extra = [t for t in (spec.get("extraArgs") or []) if str(t).strip()]
    skip_auth = bool(spec.get("skipAuth")) and phase != "firehose"

    if not programmer or not Path(programmer).is_file():
        warnings.append({"level": "error", "text": f"programmer 不存在：{programmer or '(未指定)'}"})
    if not digest or not Path(digest).is_file():
        warnings.append({"level": "error", "text": f"digest 文件不存在：{digest or '(未指定)'}"})
    if not sign or not Path(sign).is_file():
        warnings.append({"level": "error", "text": f"sign 文件不存在：{sign or '(未指定)'}"})
    if phase != "firehose" and not parts:
        warnings.append({"level": "error", "text": "没有选择或填写任何分区"})

    def fh(*a: str) -> list[str]:
        return [str(C.FH_BIN), *a, *extra]

    def add(key, title, tool, argv, *, writes=False, note="", files=None):
        steps.append({
            "id": f"{len(steps)+1:03d}-{key}", "key": key, "title": title, "tool": tool,
            "argv": argv, "preview": _preview(argv), "cwd": "{RUNDIR}",
            "writes": writes, "rescan": True, "note": note, "files": files or [],
        })

    # ---- VIP 授权链（已握手时跳过，避免重复握手）----
    # 文档 docs/VERIFIED-FLOWS.md 第 1 节实测顺序：
    # sahara → digest → verify ping → Sign.bin → sha256init
    # 少任何一步都会破坏授权状态（先发 sign 会 DevprgRSAVerify 失败）
    if not skip_auth:
        add("sahara", "Sahara · 上传签名 programmer", "QSaharaServer",
            [str(C.QS_BIN), "-p", "{PORT}", "-s", f"13:{programmer}"],
            note="programmer 写入设备 RAM，之后进入 Firehose")
        if digest:
            add("digest", "Firehose · digest 表", "fh_loader",
                fh("--port={PORT}", "--memoryname=ufs",
                   f"--signeddigests={digest}", "--testvipimpact", "--noprompt"),
                note="--signeddigests=<digest> --testvipimpact")
        add("verify", "Firehose · verify ping", "fh_loader",
                fh("--port={PORT}", "--memoryname=ufs", "--sendxml={RUNDIR}/verify.xml", "--noprompt"),
                files=[{"name": "verify.xml", "content": VERIFY_XML}])
        if sign:
            add("sign", "Firehose · 下发 Sign.bin", "fh_loader",
                fh("--port={PORT}", "--memoryname=ufs",
                   f"--signeddigests={sign}", "--testvipimpact", "--noprompt"),
                note="与 digest 同一参数，第二段签名块")
        add("sha", "Firehose · sha256init", "fh_loader",
                fh("--port={PORT}", "--memoryname=ufs", "--sendxml={RUNDIR}/sha256init.xml", "--noprompt"),
                files=[{"name": "sha256init.xml", "content": SHA_XML}])

    # ---- 分区操作 ----
    def _sort_key(p: dict):
        try:
            return (int(p.get("lun") or 0), int(p.get("start") or 0))
        except (TypeError, ValueError):
            return (99, 0)

    parts = sorted(parts, key=_sort_key)
    total_bytes = 0
    for part in parts:
        label = str(part.get("label") or "").strip()
        try:
            lun = int(part.get("lun") or 0)
            start = int(part.get("start") or 0)
            sectors = int(part.get("sectors") or 0)
        except (TypeError, ValueError):
            warnings.append({"level": "error", "text": f"分区 {label or '(空)'} 参数非法"})
            continue
        if not label or sectors <= 0:
            warnings.append({"level": "error", "text": f"分区 {label or '(空)'} 缺少 label/sectors"})
            continue
        img = str(part.get("path") or "")
        if mode == "write" and (not img or not Path(img).is_file()):
            warnings.append({"level": "error", "text": f"分区 {label} 的镜像不存在：{img or '(未指定)'}"})
            continue
        write_sectors = sectors
        if mode == "write":
            try:
                size = Path(img).stat().st_size
            except OSError:
                size = 0
            need = math.ceil(size / 4096) if size else 0
            if need > sectors:
                warnings.append({"level": "error",
                                 "text": f"镜像大于分区 {label}：{img and Path(img).name} {size/1048576:.1f} MiB "
                                         f"> 分区 {sectors*4096/1048576:.1f} MiB"})
                continue
            write_sectors = need or sectors
        total_bytes += write_sectors * 4096
        safe = label.replace("/", "_")
        blocks = (write_sectors + chunk - 1) // chunk
        cur, left = start, write_sectors
        for i in range(blocks):
            n = min(chunk, left)
            if mode == "read":
                payload = f"chunk_{safe}_{i:03d}.bin"
                xml = (f'<?xml version="1.0" ?><data>{CONFIG_PREFIX}'
                       f'<read SECTOR_SIZE_IN_BYTES="4096" filename="{payload}" '
                       f'physical_partition_number="{lun}" start_sector="{cur}" '
                       f'num_partition_sectors="{n}" label="{label}"/></data>')
                add(f"r-{safe}-{i}", f"提取 {label} {i+1}/{blocks}（LBA {cur}）", "fh_loader",
                    fh("--port={PORT}", "--memoryname=ufs",
                       f"--sendxml=x_{safe}_{i:03d}.xml", "--noprompt", "--loglevel=1"),
                    files=[{"name": f"x_{safe}_{i:03d}.xml", "content": xml}])
            else:
                payload = f"w_{safe}_{i:03d}.bin"
                xml = (f'<?xml version="1.0" ?><data>{CONFIG_PREFIX}'
                       f'<program SECTOR_SIZE_IN_BYTES="4096" filename="{payload}" '
                       f'physical_partition_number="{lun}" start_sector="{cur}" '
                       f'num_partition_sectors="{n}" label="{label}"/></data>')
                add(f"dd-{safe}-{i}", f"切块 {label} {i+1}/{blocks}", "bash",
                    ["/bin/bash", "-c",
                     f"dd if={shlex.quote(img)} of={payload} bs=4096 skip={cur - start} count={n} status=none"])
                add(f"w-{safe}-{i}", f"写入 {label} {i+1}/{blocks}（LBA {cur}）", "fh_loader",
                    fh("--port={PORT}", "--memoryname=ufs",
                       f"--sendxml=y_{safe}_{i:03d}.xml", "--noprompt",
                       "--showpercentagecomplete", "--loglevel=1"),
                    writes=True, files=[{"name": f"y_{safe}_{i:03d}.xml", "content": xml}])
            cur += n
            left -= n
        if mode == "read":
            concat = (f'import glob,hashlib,os;fs=sorted(glob.glob("chunk_{safe}_*.bin"));'
                      f'd=b"".join(open(f,"rb").read() for f in fs);'
                      f'open("dump_{safe}.img","wb").write(d);'
                      f'print("dump_{safe}.img",len(d),"bytes sha256",hashlib.sha256(d).hexdigest());'
                      f'[os.remove(f) for f in fs]')
            add(f"cat-{safe}", f"合并 {label} + sha256", "python3",
                ["/usr/bin/env", "python3", "-c", concat], note="产出 dump_<label>.img")

    errors = [w for w in warnings if w["level"] == "error"]
    return {
        "steps": steps, "warnings": warnings, "mode": mode, "phase": phase,
        "summary": {"parts": len(parts), "bytes": total_bytes, "human": _human(total_bytes),
                    "blocks": sum(1 for s in steps if "-" in s["key"] and s["key"][:2] in ("r-", "w-"))},
        "writes": mode == "write",
        "ok": not errors,
    }
