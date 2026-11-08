"""用 META/super_def.<nv>.json 里的动态分区定义合成 super.img（lpmake）。

流程：
1. 读 super_def 拿到 super 容量、group、各动态分区大小与镜像路径
2. 稀疏镜像先用 simg2img 转成 raw（lpmake 只吃 raw）
3. lpmake 组装为 IMAGES/super.img
4. 清理中间文件

只处理带 path 的槽位（通常是 _a）；_b 槽按 0 大小登记，保持分组结构一致。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SECTOR = 4096
SPARSE_MAGIC = b"\x3a\xff\x26\xed"


def _human(n: int) -> str:
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or unit == "TiB":
            return f"{v:.1f} {unit}" if unit != "B" else f"{int(v)} B"
        v /= 1024
    return f"{n} B"


def find_def(root: Path) -> Path | None:
    meta = root / "META"
    if not meta.is_dir():
        return None
    cands = sorted(meta.glob("super_def.*.json"))
    if not cands:
        return None
    # 优先用 version_info 里的 download_type（nv10010111 → 10010111）
    try:
        text = (root / "version_info.txt").read_text(errors="replace")
        m = re.search(r'"download_type"\s*:\s*"nv(\d+)"', text)
        if m:
            want = meta / f"super_def.{m.group(1)}.json"
            if want.is_file():
                return want
    except OSError:
        pass
    return cands[0]


def _to_raw(src: Path, dst: Path, log) -> Path:
    """稀疏镜像转 raw；本身已是 raw 时直接复制（避免跨设备硬链接问题）。"""
    with src.open("rb") as f:
        magic = f.read(4)
    if magic == SPARSE_MAGIC:
        log(f"  反稀疏 {src.name} → {dst.name}")
        subprocess.run(["simg2img", str(src), str(dst)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    else:
        shutil.copyfile(src, dst)
    return dst


def build(dirpath: str, log=None) -> dict:
    say = log or (lambda _m: None)
    root = Path(dirpath).expanduser().resolve()
    imgs = root / "IMAGES" if (root / "IMAGES").is_dir() else root
    def_path = find_def(root)
    if not def_path:
        return {"ok": False, "error": "包内没有 META/super_def.*.json，无法合成 super"}
    if not shutil.which("lpmake"):
        return {"ok": False, "error": "系统缺少 lpmake（android-sdk-libsparse-utils）"}

    spec = json.loads(def_path.read_text())
    block = (spec.get("block_devices") or [{}])[0]
    device_size = int(block.get("size") or 0)
    meta_size = int((spec.get("super_meta") or {}).get("size") or 65536)
    block_size = int(block.get("block_size") or SECTOR)
    if not device_size:
        return {"ok": False, "error": "super_def 里缺少 block_devices.size"}

    groups = spec.get("groups") or []
    parts = spec.get("partitions") or []

    say(f"定义：{def_path.name} · super {_human(device_size)} · {len(parts)} 个逻辑分区")

    out = imgs / "super.img"
    work = Path(tempfile.mkdtemp(prefix="superbuild-", dir=str(imgs)))
    args = [
        "lpmake",
        f"--device-size={device_size}",
        f"--metadata-size={meta_size}",
        "--metadata-slots=2",
        f"--block-size={block_size}",
        "--super-name=super",
    ]
    for g in groups:
        # lpmake 的 --group 必须带容量；无名/无容量的 default 组直接用隐式默认组
        name = g.get("name") or "default"
        if g.get("maximum_size"):
            args.append(f"--group={name}:{int(g['maximum_size'])}")

    total = 0
    built = []
    try:
        for p in parts:
            name = p.get("name")
            if not name:
                continue
            group = p.get("group_name") or "default"
            size = int(p.get("size") or 0)
            rel = p.get("path")
            src = root / rel if rel else None
            if src and src.is_file():
                raw = _to_raw(src, work / f"{name}.img", say)
                size = max(size, raw.stat().st_size)
                args += [f"--partition={name}:readonly:{size}:{group}",
                         f"--image={name}={raw}"]
                total += size
                built.append(name)
            else:
                args.append(f"--partition={name}:readonly:0:{group}")

        say(f"组装：{len(built)} 个镜像 · {_human(total)}")
        args += [f"--output={out}"]
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
            return {"ok": False, "error": "lpmake 失败：" + " | ".join(tail), "log": proc.stdout}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if not out.is_file():
        return {"ok": False, "error": "lpmake 未产出 super.img"}
    size = out.stat().st_size
    say(f"完成：{out.name} · {_human(size)}")
    return {
        "ok": True,
        "path": str(out),
        "size": size,
        "human": _human(size),
        "partitions": built,
        "def": def_path.name,
    }
