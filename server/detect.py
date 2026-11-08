"""Environment probing: tools, serial ports, permissions, udev rules."""
from __future__ import annotations

import grp
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config as C

_TOOL_CACHE: dict[str, object] = {}
_TOOL_CACHE_TS = 0.0
_TOOL_TTL = 20.0


def _read(path: str | Path) -> str:
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return ""


def _run(cmd: list[str], timeout: float = 6.0) -> str:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, errors="replace",
        )
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _tool_signature(path: Path) -> dict:
    """Version banner only: both vendor binaries print their build stamp in the usage text."""
    info = {"path": str(path), "exists": path.exists(), "executable": os.access(path, os.X_OK),
            "version": "", "build": "", "size": 0}
    if not info["exists"]:
        return info
    try:
        info["size"] = path.stat().st_size
    except OSError:
        pass
    out = _run([str(path), "--help"])
    for rx, key in ((r"Base Version:\s*(\S+)", "version"),
                    (r"Binary build date:\s*(.+)", "build"),
                    (r"Incremental Build version:\s*(\S+)", "build")):
        m = re.search(rx, out)
        if m and not info[key]:
            info[key] = m.group(1).strip()
    if not info["version"] and info["build"]:
        info["version"] = info["build"]
    return info


def _read_cmd(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL, errors="replace")
        return (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def tool_info(force: bool = False) -> dict:
    global _TOOL_CACHE_TS
    now = time.time()
    if force or not _TOOL_CACHE or now - _TOOL_CACHE_TS > _TOOL_TTL:
        _TOOL_CACHE.clear()
        _TOOL_CACHE.update({
            "qsahara": _tool_signature(C.QS_BIN),
            "fh_loader": _tool_signature(C.FH_BIN),
            "checked_at": now,
        })
        _TOOL_CACHE_TS = now
    return dict(_TOOL_CACHE)


# --------------------------------------------------------------------------- ports

def _usb_attrs(tty_sys: Path) -> dict:
    """Walk up /sys/class/tty/<name>/device until a USB device with idVendor appears."""
    out = {"vid": "", "pid": "", "manufacturer": "", "product": "", "serial": ""}
    try:
        node = (tty_sys / "device").resolve()
    except OSError:
        return out
    for _ in range(6):
        if (node / "idVendor").exists():
            out["vid"] = _read(node / "idVendor").lower()
            out["pid"] = _read(node / "idProduct").lower()
            out["manufacturer"] = _read(node / "manufacturer")
            out["product"] = _read(node / "product")
            out["serial"] = _read(node / "serial")
            break
        if node.parent == node:
            break
        node = node.parent
    return out


def list_ports() -> list[dict]:
    ports: list[dict] = []
    for base in ("ttyUSB", "ttyACM", "ttyHS"):
        for sysdir in sorted(Path("/sys/class/tty").glob(f"{base}*")):
            dev = Path("/dev") / sysdir.name
            attrs = _usb_attrs(sysdir)
            label, edl = C.KNOWN_USB.get((attrs["vid"], attrs["pid"]), ("", False))
            try:
                writable = os.access(dev, os.R_OK | os.W_OK)
            except OSError:
                writable = False
            ports.append({
                "path": str(dev),
                "name": sysdir.name,
                "exists": dev.exists(),
                "writable": writable,
                "vid": attrs["vid"], "pid": attrs["pid"],
                "manufacturer": attrs["manufacturer"], "product": attrs["product"],
                "serial": attrs["serial"],
                "mode": label,
                "is_edl": edl,
            })
    return ports


def pick_port(ports: list[dict] | None = None) -> str | None:
    ports = ports if ports is not None else list_ports()
    for p in ports:
        if p["is_edl"] and p["exists"]:
            return p["path"]
    for p in ports:
        if p["exists"] and p["writable"]:
            return p["path"]
    return ports[0]["path"] if ports else None


# --------------------------------------------------------------------------- permissions

def _in_group(name: str) -> bool:
    try:
        gid = grp.getgrnam(name).gr_gid
    except KeyError:
        return False
    if os.getgid() == gid:
        return True
    try:
        return gid in os.getgroups()
    except OSError:
        return False


def permissions() -> dict:
    rules_dir = Path("/etc/udev/rules.d")
    rules = sorted(p.name for p in rules_dir.glob("*edl*")) if rules_dir.is_dir() else []
    return {
        "user": os.environ.get("USER", ""),
        "is_root": os.geteuid() == 0,
        "in_dialout": _in_group("dialout") or _in_group("uucp"),
        "udev_rules": rules,
        "udev_installed": any("9008" in r or "qualcomm" in r.lower() for r in _all_rules(rules_dir)),
        "installer": str(C.ROOT / "scripts" / "udev-9008.rules"),
        "install_hint": ("sudo install -m644 scripts/udev-9008.rules "
                         "/etc/udev/rules.d/51-qualcomm-9008.rules && "
                         "sudo udevadm control --reload-rules && sudo udevadm trigger"),
    }


def _all_rules(rules_dir: Path) -> list[str]:
    try:
        return [p.name.lower() for p in rules_dir.glob("*.rules")]
    except OSError:
        return []


def environment() -> dict:
    return {
        "root": str(C.ROOT),
        "tools": tool_info(),
        "ports": list_ports(),
        "suggested_port": pick_port(),
        "permissions": permissions(),
        "platform": os.uname().sysname + " " + os.uname().release,
        "data_dir": str(C.DATA_DIR),
    }


