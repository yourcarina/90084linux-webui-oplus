"""Static paths / constants for the EDL Bench wrapper."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("EDL_BENCH_ROOT", Path(__file__).resolve().parent.parent)).resolve()
QS_BIN = Path(os.environ.get("EDL_QS_BIN", str(ROOT / "QSaharaServer")))
FH_BIN = Path(os.environ.get("EDL_FH_BIN", str(ROOT / "fh_loader")))
VENV_PY = Path(os.environ.get("EDL_VENV_PY", str(ROOT / ".venv" / "bin" / "python")))

DATA_DIR = Path(os.environ.get("EDL_DATA_DIR", str(ROOT / "data")))
PROFILE_DIR = DATA_DIR / "profiles"
RUN_DIR = DATA_DIR / "runs"
WEB_DIST = ROOT / "web" / "dist"

for _p in (PROFILE_DIR, RUN_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# Qualcomm / vendor USB ids that signal "the phone is in a download mode".
KNOWN_USB = {
    ("05c6", "9008"): ("QC EDL / 9008", True),
    ("05c6", "900e"): ("QC EDL (900E)", True),
    ("05c6", "9006"): ("QC Memory Debug (9006)", True),
    ("05c6", "901d"): ("QC diag,adb (901d)", True),
    ("05c6", "9091"): ("QC diag (9091)", False),
    ("05c6", "9092"): ("QC diag (9092)", False),
    ("1004", "61a1"): ("LG Memory Debug", True),
    ("1199", "9071"): ("Sierra Wireless", True),
    ("1199", "9070"): ("Sierra Wireless", True),
}

# Sahara image ids as printed by QSaharaServer --help (default mapping table).
SAHARA_IDS = {
    2: "amss.mbn", 6: "apps.mbn", 8: "dsp1.mbn", 10: "dbl.mbn", 11: "osbl.mbn",
    12: "dsp2.mbn", 13: "prog_firehose (loader)", 16: "efs1.mbn", 17: "efs2.mbn",
    20: "efs3.mbn", 21: "sbl1.mbn", 22: "sbl2.mbn", 23: "rpm.mbn", 25: "tz.mbn",
    28: "dsp3.mbn", 29: "acdb.mbn", 30: "wdt.mbn", 31: "mba.mbn",
}
