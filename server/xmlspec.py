"""Parse Qualcomm rawprogram/patch XML so the UI can show an inventory before flashing."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path


def _fmt_bytes(n: int) -> str:
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or unit == "TiB":
            return f"{v:.1f} {unit}" if unit != "B" else f"{int(v)} B"
        v /= 1024
    return f"{n} B"




def dir_stats(imagedir: str) -> dict:
    if not imagedir or not os.path.isdir(imagedir):
        return {"exists": False, "files": 0, "bytes": 0, "human": "0 B"}
    files = 0
    total = 0
    for entry in os.scandir(imagedir):
        if entry.is_file():
            files += 1
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return {"exists": True, "files": files, "bytes": total, "human": _fmt_bytes(total)}
