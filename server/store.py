"""任务目录与元数据的落盘（纯 JSON，不引数据库）。"""
from __future__ import annotations

import json
from pathlib import Path

from . import config as C


def run_dir(run_id: str) -> Path:
    d = C.RUN_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_run_meta(run_id: str, meta: dict) -> None:
    d = run_dir(run_id)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))


def append_run_log(run_id: str, line: str) -> None:
    d = run_dir(run_id)
    with (d / "run.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
