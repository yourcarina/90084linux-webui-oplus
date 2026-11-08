"""解析固件目录里的 rawprogram*.xml，得到可直接用于回读/写入的分区清单。

字段来自 XML 的 <program> 标签（Qualcomm 标准）：
  label                       分区名
  physical_partition_number   LUN
  start_sector                起始 4K/512B 扇区（按 SECTOR_SIZE_IN_BYTES 决定）
  num_partition_sectors       长度
  filename                    对应镜像文件名
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from . import xmlspec


def _fmt(n: int) -> str:
    v = float(n)
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or u == "TiB":
            return f"{v:.1f} {u}" if u != "B" else f"{int(v)} B"
        v /= 1024
    return f"{n} B"


def scan_dir(directory: str, files: list[str] | None = None) -> dict:
    target = Path(os.path.expanduser(directory or "")).resolve()
    file_list = [Path(os.path.expanduser(f)).resolve() for f in (files or []) if f]
    if file_list:
        target = file_list[0].parent
    elif not target.is_dir():
        return {"dir": str(target), "exists": False, "partitions": [], "xmls": [], "warnings": ["目录不存在"]}

    xmls = file_list if file_list else sorted(target.glob("rawprogram*.xml"))
    partitions: list[dict] = []
    warnings: list[str] = []
    seen: set[tuple[str, int]] = set()

    for xml in xmls:
        try:
            root = ET.parse(xml).getroot()
        except ET.ParseError as exc:
            warnings.append(f"{xml.name} 解析失败：{exc}")
            continue
        sector_size = 4096
        for prog in root.iter("program"):
            try:
                sector_size = int(prog.get("SECTOR_SIZE_IN_BYTES") or 4096)
            except ValueError:
                pass
            label = (prog.get("label") or "").strip()
            if not label:
                continue
            lun = int(prog.get("physical_partition_number") or 0)
            start = int(prog.get("start_sector") or 0)
            try:
                sectors = int(float(prog.get("num_partition_sectors") or 0))
            except ValueError:
                sectors = 0
            fname = (prog.get("filename") or "").strip()
            fpath = target / fname if fname else None
            present = bool(fpath and fpath.is_file())
            key = (label, lun)
            if key in seen:
                continue
            seen.add(key)
            size = sectors * sector_size
            if present and fpath:
                try:
                    size = fpath.stat().st_size
                except OSError:
                    pass
            partitions.append({
                "label": label, "lun": lun, "start": start, "sectors": sectors,
                "sectorSize": sector_size, "file": fname,
                "path": str(fpath) if fpath else "",
                "present": present, "size": size, "human": _fmt(size),
                "xml": xml.name,
            })

    if not xmls:
        warnings.append("目录内没有 rawprogram*.xml")
    if partitions and not any(p["present"] for p in partitions):
        warnings.append("XML 里的镜像文件都不在目录中（回读不受影响，刷入需要）")

    stats = xmlspec.dir_stats(str(target))
    return {
        "dir": str(target),
        "exists": True,
        "xmls": [x.name for x in xmls],
        "partitions": partitions,
        "count": len(partitions),
        "bytes": sum(p["size"] for p in partitions),
        "human": _fmt(sum(p["size"] for p in partitions)),
        "stats": stats,
        "warnings": warnings,
    }


def auto_match(directory: str) -> dict:
    """在目录里自动匹配 programmer / digest / sign / rawprogram / patch。"""
    target = Path(os.path.expanduser(directory or "")).resolve()
    if not target.is_dir():
        return {"dir": str(target), "exists": False}
    def pick(*patterns):
        for pat in patterns:
            hits = sorted(target.glob(pat))
            if hits:
                return [str(h) for h in hits]
        return []
    return {
        "dir": str(target),
        "exists": True,
        "programmer": (pick("*.melf", "prog_firehose*.elf", "prog_firehose*.mbn") or [None])[0],
        "digest": (pick("*igest*.elf", "*igest*.mbn", "*igest*.bin") or [None])[0],
        "sign": (pick("*ign*.bin", "*ignature*.bin") or [None])[0],
        "rawprograms": pick("rawprogram*.xml"),
        "patch": pick("patch*.xml"),
    }
