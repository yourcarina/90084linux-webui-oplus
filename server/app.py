"""EDL//BENCH —— 精简版后端：VIP 授权链 + 分区提取/写入。"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from . import detect, flow, fwparse, package, session, superbuild
from .runner import manager

app = FastAPI(title="EDL Bench", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TERMINAL = {"done", "failed", "cancelled"}
def _restart_cmd() -> list[str]:
    """用 `python -m uvicorn ...` 形式重启。

    不能复用 sys.argv：以 `python -m uvicorn` 启动时 argv[0] 指向
    site-packages/uvicorn/__main__.py，会把这个目录插到 sys.path 前面，
    导致 `import logging` 命中 uvicorn/logging.py 而崩掉。
    """
    argv = sys.argv
    host, port = "127.0.0.1", "8765"
    for i, a in enumerate(argv):
        if a == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
        elif a == "--port" and i + 1 < len(argv):
            port = argv[i + 1]
    return [sys.executable, "-m", "uvicorn", "server.app:app", "--host", host, "--port", port]


RESTART_CMD = _restart_cmd()
EDL_BINS = ("QSaharaServer", "fh_loader", "fh_loader_oppo")


def _kill_edl() -> list[int]:
    """杀掉所有残留的 EDL 工具进程。"""
    killed: list[int] = []
    base = "/proc"
    try:
        entries = os.listdir(base)
    except OSError:
        return killed
    me = os.getpid()
    for name in entries:
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"{base}/{name}/cmdline", "rb") as fh:
                argv = fh.read().decode(errors="replace").split("\0")
        except OSError:
            continue
        if argv and os.path.basename(argv[0]) in EDL_BINS:
            try:
                os.kill(int(name), 9)
                killed.append(int(name))
            except (ProcessLookupError, PermissionError):
                pass
    return killed


async def _delayed_restart() -> None:
    await asyncio.sleep(0.8)                        # 先把响应发回去
    log = open(C.DATA_DIR / "server.log", "ab", buffering=0)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C.ROOT)
    env.pop("PYTHONSTARTUP", None)
    subprocess.Popen(RESTART_CMD, cwd=str(C.ROOT), env=env, stdin=subprocess.DEVNULL,
                     stdout=log, stderr=log, start_new_session=True)
    os._exit(0)


@app.get("/api/env")
async def env() -> dict:
    data = detect.environment()
    data["dataDir"] = str(C.DATA_DIR)
    data["fh_bin"] = str(C.FH_BIN)
    return data


@app.get("/api/ports")
async def ports() -> dict:
    items = detect.list_ports()
    return {"ports": items, "resolved": detect.pick_port(items)}


@app.post("/api/fs/list")
async def fs_list(payload: dict = Body(...)) -> dict:
    raw = str(payload.get("path") or "").strip()
    target = Path(os.path.expanduser(raw)) if raw else Path.home()
    if not target.is_absolute():
        target = C.ROOT / target
    target = target.resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在：{target}")
    if target.is_file():
        target = target.parent
    entries = []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if entry.name.startswith("."):
                continue
            try:
                st = entry.stat()
                size, mtime = st.st_size, st.st_mtime
            except OSError:
                size, mtime = 0, 0
            entries.append({"name": entry.name, "path": str(entry), "dir": entry.is_dir(),
                            "size": size, "mtime": mtime, "ext": entry.suffix.lower()})
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"无权限读取：{target}")
    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "entries": entries,
        "roots": [{"name": "主目录", "path": str(Path.home())},
                  {"name": "工作区", "path": str(C.ROOT)},
                  {"name": "/", "path": "/"}],
    }


@app.get("/api/session")
async def session_get(port: str | None = None) -> dict:
    return session.status(port or detect.pick_port())




@app.post("/api/firmware/auto")
async def firmware_auto(payload: dict = Body(...)) -> dict:
    return fwparse.auto_match(str(payload.get("dir") or ""))


@app.post("/api/package/super")
def package_super(payload: dict = Body(...)) -> dict:
    return superbuild.build(str(payload.get("dir") or ""))


@app.post("/api/package/scan")
async def package_scan(payload: dict = Body(...)) -> dict:
    return package.scan(str(payload.get("dir") or ""))


@app.post("/api/firmware/scan")
async def firmware_scan(payload: dict = Body(...)) -> dict:
    return fwparse.scan_dir(str(payload.get("dir") or ""), payload.get("files") or None)



def _with_session_state(spec: dict) -> dict:
    """已在 Firehose 的会话：提取/写入直接下发，不再重跑授权链。"""
    phase = str(spec.get("phase") or spec.get("mode") or "")
    port = spec.get("port") or detect.pick_port()
    if phase in ("read", "write") and port:
        if session.status(port)["state"] in ("authed", "programmer", "firehose"):
            return {**spec, "skipAuth": True}
    return spec

@app.post("/api/flow/plan")
async def flow_plan(payload: dict = Body(...)) -> dict:
    return flow.build_job(_with_session_state(payload.get("spec") or payload))


@app.post("/api/flow/run")
async def flow_run(payload: dict = Body(...)) -> dict:
    spec = payload.get("spec") or payload
    built = flow.build_job(spec)
    running = [j for j in (manager.get(x["id"]) for x in manager.all())
               if j is not None and j.status in ("running", "starting")]
    if running:
        raise HTTPException(status_code=409, detail={
            "message": f"任务进行中 · {running[0].id}",
            "errors": ["concurrent job"]})
    spec = _with_session_state(spec)
    target_port = spec.get("port") or detect.pick_port()
    # 已授权会话再点"进入 FIREHOSE"没有意义：重复下发签名块会被设备拒绝
    if str(spec.get("phase") or "") == "firehose" and session.status(target_port)["state"] in ("authed", "programmer"):
        return {"id": None, "already": True,
                "message": "已授权 · 无需重复握手，可直接提取或写入"}
    if not target_port:
        raise HTTPException(status_code=409, detail={
            "message": "无设备 · 等待 /dev/ttyUSB* 出现",
            "errors": ["no port"]})
    busy = session.port_busy(target_port)
    if busy:
        raise HTTPException(status_code=409, detail={
            "message": f"端口占用 · {busy}",
            "errors": [busy]})
    rec = session.status(target_port)
    if rec["state"] == "dirty" and not payload.get("force"):
        raise HTTPException(status_code=409, detail={
            "message": f"会话失效 · {rec['note']}；{session.RECOVER_HINT}",
            "errors": [rec["note"]], "dirty": True, "hint": session.RECOVER_HINT})
    if not built["steps"]:
        raise HTTPException(status_code=400, detail="计划为空")
    if not built["ok"]:
        raise HTTPException(status_code=400, detail={
            "message": "参数校验未通过",
            "errors": [w["text"] for w in built["warnings"] if w["level"] == "error"]})
    plan = {
        "steps": built["steps"], "warnings": built["warnings"],
        "inventory": {"count": 0, "bytes": built["summary"]["bytes"],
                      "human": built["summary"]["human"], "missing": [], "images": [], "unscanned": []},
        "port": {"resolved": spec.get("port") or None, "automatic": not spec.get("port"), "present": True},
        "profile": {"name": f"job-{built['mode']}", "device": {"waitSeconds": 0}},
        "writes": built["writes"], "ok": True,
        "summary": {"steps": len(built["steps"]), "images": built["summary"]["parts"],
                    "bytes": built["summary"]["bytes"], "human": built["summary"]["human"],
                    "errors": 0, "warnings": len(built["warnings"])},
    }
    session.set_state(target_port, "clean", "任务开始")
    job = manager.create(plan, dry_run=bool(payload.get("dryRun")), continue_on_error=False)
    await job.start()
    return {"id": job.id, "plan": plan}


@app.post("/api/device/reset")
async def device_reset(payload: dict = Body(default={})) -> dict:
    port = (payload or {}).get("port") or detect.pick_port()
    if not port:
        raise HTTPException(status_code=409, detail="没有可用串口，无法复位设备")
    xml = C.DATA_DIR / "reset.xml"
    xml.parent.mkdir(parents=True, exist_ok=True)
    xml.write_text('<?xml version="1.0" ?><data>'
                   '<configure MemoryName="ufs" ModeType="0" EnableFlash="1" />'
                   '<power value="reset"/></data>')
    proc = subprocess.run([str(C.FH_BIN), f"--port={port}", "--memoryname=ufs",
                           f"--sendxml={xml}", "--noprompt", "--loglevel=1"],
                          cwd=str(C.DATA_DIR), capture_output=True, text=True, timeout=25)
    out = (proc.stdout or "") + (proc.stderr or "")
    ok = "power" in out.lower() or proc.returncode == 0
    session.set_state(port, "absent", "已下发复位")
    return {"ok": ok, "port": port, "output": out[-400:]}


@app.post("/api/admin/restart")
async def admin_restart() -> dict:
    killed = _kill_edl()
    for item in manager.all():
        job = manager.get(item["id"])
        if job is not None and job.status in ("running", "starting"):
            await job.cancel()
    asyncio.create_task(_delayed_restart())
    return {"ok": True, "killed": killed, "restarting": True}


@app.get("/api/jobs/active")
async def jobs_active() -> dict:
    jobs = [manager.get(j["id"]) for j in manager.all()]
    jobs = [j for j in jobs if j is not None and j.status not in ("done", "failed", "cancelled")]
    if not jobs:
        return {"active": None}
    job = max(jobs, key=lambda j: j.created)
    snap = job.snapshot()
    snap["events"] = list(job.events)
    return {"active": snap}


@app.get("/api/runs/{job_id}")
async def runs_get(job_id: str) -> dict:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    snap = job.snapshot()
    snap["events"] = list(job.events)[-800:]
    return snap


@app.get("/api/runs/{job_id}/events")
async def runs_events(job_id: str, request: Request) -> StreamingResponse:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    queue = job.subscribe()

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=12)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                if ev.get("type") == "job" and ev.get("status") in TERMINAL:
                    break
        finally:
            job.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/runs/{job_id}/cancel")
async def runs_cancel(job_id: str) -> dict:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await job.cancel()
    return {"ok": True, "status": job.status}


if C.WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=C.WEB_DIST / "assets"), name="assets")


@app.get("/{path:path}")
async def spa(path: str):
    index = C.WEB_DIST / "index.html"
    if path.startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    if path and (C.WEB_DIST / path).is_file():
        return FileResponse(C.WEB_DIST / path)
    if index.is_file():
        return FileResponse(index)
    return JSONResponse({"detail": "前端未构建：cd web && npm run build"}, status_code=503)
