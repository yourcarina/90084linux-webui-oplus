"""解析 OPPO/OnePlus 9008 整包（OFP 结构）。

包结构：
  版本目录/
    version_info.txt        # 型号 / 版本 / 平台
    build.prop
    IMAGES/                 # 镜像 + gpt_main*.bin + patch*.xml
    META/                   # 分区元数据（ab_partitions.txt 等）

分区表来源：IMAGES/gpt_main{N}.bin（N 即 LUN），4K 扇区。
镜像匹配：label 去掉 _a/_b 后缀后按文件名主干匹配（boot_a → boot.img）。
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

SECTOR = 4096
GPT_SIG = b"EFI PART"

# label 去掉 A/B 后缀
_AB = re.compile(r"_(a|b)$", re.I)


def _human(n: int) -> str:
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or unit == "TiB":
            return f"{v:.1f} {unit}" if unit != "B" else f"{int(v)} B"
        v /= 1024
    return f"{n} B"


def parse_gpt(path: Path) -> list[dict]:
    """从 gpt_main{N}.bin 里读出分区条目（起点/扇区数）。"""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    hdr_off = None
    for cand in (SECTOR, 512, 0):
        if data[cand:cand + 8] == GPT_SIG:
            hdr_off = cand
            break
    if hdr_off is None:
        return []

    hdr = data[hdr_off:hdr_off + 92]
    entries_lba = struct.unpack_from("<Q", hdr, 72)[0]
    count = struct.unpack_from("<I", hdr, 80)[0]
    esize = struct.unpack_from("<I", hdr, 84)[0] or 128
    base = entries_lba * SECTOR

    out: list[dict] = []
    for i in range(count):
        off = base + i * esize
        if off + 128 > len(data):
            break
        ent = data[off:off + 128]
        if ent[0:16] == b"\x00" * 16:
            continue
        first, last = struct.unpack_from("<QQ", ent, 32)
        label = ent[56:128].decode("utf-16-le").rstrip("\x00")
        if not label:
            continue
        out.append({"label": label, "start": first, "sectors": last - first + 1})
    return out


def _version(dirpath: Path) -> tuple[str, str]:
    text = ""
    for name in ("version_info.txt", "build.prop"):
        p = dirpath / name
        if p.is_file():
            try:
                text += p.read_text(errors="replace")
            except OSError:
                pass
    model = ""
    version = ""
    m = re.search(r'"version_name"\s*:\s*"([^"]+)"', text)
    if m:
        version = m.group(1)
    m = re.search(r'"platform"\s*:\s*"([^"]+)"', text)
    platform = m.group(1) if m else ""
    m = re.search(r"ro\.product\.(?:model|device|name)=(\S+)", text)
    if m:
        model = m.group(1)
    if not version:
        version = dirpath.name
    return model, version if not platform else f"{version} ({platform})"


def _parse_rawprogram(path: Path) -> list[dict]:
    """rawprogram{N}.xml 是包的权威分区表（含 label / LUN / 起始 / 扇区 / 镜像）。"""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return []
    m = re.search(r"rawprogram(\d+)\.xml$", path.name)
    lun_hint = int(m.group(1)) if m else None
    out = []
    for p in root.iter("program"):
        label = (p.get("label") or "").strip()
        if not label:
            continue
        try:
            lun = int(p.get("physical_partition_number") or (lun_hint if lun_hint is not None else 0))
            start = int(p.get("start_sector") or 0)
            sectors = int(float(p.get("num_partition_sectors") or 0))
        except ValueError:
            continue
        out.append({
            "label": label, "lun": lun, "start": start, "sectors": sectors,
            "filename": (p.get("filename") or "").strip(),
        })
    return out


def scan(dirpath: str) -> dict:
    root = Path(dirpath).expanduser().resolve()
    img_dir = root / "IMAGES" if (root / "IMAGES").is_dir() else root
    if not img_dir.is_dir():
        return {"exists": False, "dir": str(root), "partitions": [], "warnings": ["目录不存在"]}

    raws = sorted(p for p in img_dir.glob("rawprogram*.xml")
                  if re.fullmatch(r"rawprogram\d+\.xml", p.name))
    gpts = sorted(img_dir.glob("gpt_main*.bin"))

    rows: list[dict] = []
    source = ""
    if raws:
        source = "rawprogram"
        for rp in raws:
            rows.extend(_parse_rawprogram(rp))
    elif gpts:
        source = "gpt"
        for g in gpts:
            m = re.search(r"gpt_main(\d+)\.bin$", g.name)
            if not m:
                continue
            lun = int(m.group(1))
            for ent in parse_gpt(g):
                rows.append({**ent, "lun": lun, "filename": ""})

    if not rows:
        return {"exists": True, "dir": str(root), "partitions": [],
                "warnings": ["包内既没有 rawprogram*.xml 也没有可解析的 gpt_main*.bin"]}

    # 镜像是否在包内
    missing: list[str] = []
    optional_missing: list[str] = []
    for r in rows:
        fn = r.get("filename") or ""
        path = img_dir / fn if fn else None
        present = bool(path and path.is_file())
        size = r["sectors"] * SECTOR
        if present and path is not None:
            size = path.stat().st_size
        elif fn:
            # 小分区（ocdt/secdata 等）缺镜像不阻断整体写入，单独标注
            (optional_missing if r["sectors"] <= 64 else missing).append(fn)
        r.update({
            "size": size,
            "human": _human(size),
            "image": str(path) if present and path else "",
            "imageName": fn,
            "present": present,
        })
        r.pop("filename", None)

    # 包内存在但未被 rawprogram 引用的镜像（多为动态分区，需写入 super）
    referenced = {r["imageName"].lower() for r in rows if r["imageName"]}
    unreferenced = []
    for f in img_dir.iterdir():
        if not f.is_file() or f.suffix.lower() not in (".img", ".bin", ".elf", ".mbn"):
            continue
        if f.name.lower() in referenced or f.name.startswith("gpt_"):
            continue
        if f.stat().st_size < 1024 * 1024:
            continue
        unreferenced.append({"name": f.name, "size": f.stat().st_size, "human": _human(f.stat().st_size)})
    unreferenced.sort(key=lambda x: -x["size"])

    model, version = _version(root)
    usable = [r for r in rows if r["present"]]
    return {
        "exists": True,
        "dir": str(root),
        "imagesDir": str(img_dir),
        "source": source,
        "model": model,
        "version": version,
        "count": len(rows),
        "bytes": sum(r["size"] for r in usable),
        "human": _human(sum(r["size"] for r in usable)),
        "withImage": len(usable),
        "missing": sorted(set(missing)),
        "optionalMissing": sorted(set(optional_missing)),
        "unreferenced": unreferenced,
        "partitions": rows,
        "warnings": [],
    }
