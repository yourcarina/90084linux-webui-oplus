"""脏会话探测：记录每条 EDL 会话的状态，避免用户在死会话上反复重试。

状态机：
  clean            刚进入 9008，可以正常握手
  programmer       已上传 programmer（Sahara 成功）
  authed           授权链走完（digest/verify/sign/sha256init）
  dirty            出现不可恢复的迹象（重复授权、签名失败、garbage 等）→ 必须冷启动
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import config as C

STATE_FILE = C.DATA_DIR / "session.json"

# 出现这些字样说明当前会话已经污染，重试没有意义
DIRTY_SIGNATURES = {
    "Too many concurrent open SHA handles": "签名句柄未释放，必须冷启动",
    "target responded with garbage": "串口返回垃圾数据，会话已被写坏",
}

# 这些 ERROR 是设备正常回话的一部分，不能算失败
BENIGN = (
    "mode= invalid value",
    "enableflash not found",
    "use_wakelock",
    "failed to open wake lock",
    "invalid value",
)

RECOVER_HINT = "长按电源 15 秒彻底断电，再重新进入 9008"


def _port_serial(port: str | None) -> str:
    """从 sysfs 读该串口对应 USB 设备的 product 串（含 SN），用来判断是不是同一台设备。"""
    if not port:
        return ""
    name = Path(port).name
    base = Path("/sys/class/tty") / name / "device"
    try:
        node = base.resolve()
    except OSError:
        return ""
    for parent in [node, *node.parents]:
        prod = parent / "product"
        if prod.exists():
            try:
                return prod.read_text().strip()
            except OSError:
                return ""
    return ""


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(STATE_FILE)


def get(port: str | None) -> dict:
    data = _load()
    rec = data.get(port or "", {})
    return {
        "port": port,
        "state": rec.get("state", "unknown"),
        "note": rec.get("note", ""),
        "updated": rec.get("updated", 0),
        "hint": RECOVER_HINT if rec.get("state") == "dirty" else "",
    }


def set_state(port: str | None, state: str, note: str = "") -> dict:
    if not port:
        return get(port)
    data = _load()
    try:
        seen = Path(port).stat().st_ctime
    except OSError:
        seen = 0
    data[port] = {"state": state, "note": note, "updated": time.time(),
                  "seen": seen, "serial": _port_serial(port)}
    _save(data)
    return get(port)


def note_output(port: str | None, line: str) -> dict | None:
    """扫描一条输出，只有命中真正的脏会话特征才置 dirty。"""
    low = line.lower()
    if any(b in low for b in BENIGN):
        return None
    for sig, why in DIRTY_SIGNATURES.items():
        if sig.lower() in low:
            note = f"{why}（{sig}）"
            return set_state(port, "dirty", note)
    return None


def port_busy(port: str | None) -> str | None:
    """端口被别的进程占着（多为上一次没退干净的 fh_loader/QSaharaServer）。"""
    if not port or not Path(port).exists():
        return None
    try:
        out = subprocess.run(["fuser", port], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    pids = [p for p in (out.stdout + out.stderr).split() if p.isdigit()]
    if pids:
        return f"端口被进程 {', '.join(pids[:4])} 占用"
    return None


def status(port: str | None) -> dict:
    """只读状态：不向设备发任何数据（对 Sahara 态设备发数据会打断握手）。"""
    if not port or not Path(port).exists():
        return {"port": port, "state": "absent", "note": "串口不存在（设备不在 9008）", "hint": ""}
    busy = port_busy(port)
    if busy:
        return {"port": port, "state": "busy", "note": busy, "hint": ""}
    rec = get(port)
    try:
        seen_now = Path(port).stat().st_ctime
    except OSError:
        seen_now = 0
    raw = _load().get(port or "", {})
    cur_serial = _port_serial(port)
    stale = bool(raw.get("serial")) and cur_serial and raw["serial"] != cur_serial
    if not raw.get("serial"):
        stale = (not raw.get("seen")) or (seen_now and abs(raw["seen"] - seen_now) > 0.5)
    if stale:
        # 端口重新枚举过 → 上一个会话的记录作废
        rec = {**rec, "state": "edl", "note": "设备刚重新枚举（新会话）", "hint": ""}
    elif rec["state"] in ("unknown", "clean"):
        rec = {**rec, "state": "edl", "note": "设备在 9008（尚未进入 Firehose）"}
    return rec
