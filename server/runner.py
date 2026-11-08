"""Async job runner: sequential step execution with live SSE log streaming."""
from __future__ import annotations

import asyncio
import os
import re
import signal
import time
import uuid
from collections import deque

from . import config as C
from . import detect, session, store

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
LINE_SPLIT = re.compile(rb"\r\n|\n|\r")

# QSaharaServer 在设备已处于 Firehose 时会报这些，等价于"握手已完成过"
ALREADY_FIREHOSE = re.compile(
    r"Received a different command|while waiting for hello packet", re.I)


def _clean(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return ANSI_RE.sub("", text).rstrip()


def _now() -> float:
    return time.time()


class StepState(dict):
    pass


class Job:
    def __init__(self, job_id: str, plan: dict, dry_run: bool, continue_on_error: bool = False):
        self.id = job_id
        self.plan = plan
        self.dry_run = dry_run
        self.continue_on_error = continue_on_error
        self.status = "starting"
        self.created = _now()
        self.started: float | None = None
        self.ended: float | None = None
        self.port: str | None = plan.get("port", {}).get("resolved")
        self.steps: list[dict] = []
        for step in plan.get("steps", []):
            self.steps.append({
                **step,
                "status": "pending",
                "exitCode": None,
                "started": None,
                "ended": None,
                "port": None,
            })
        self.events: deque[dict] = deque(maxlen=4000)
        self.subscribers: set[asyncio.Queue] = set()
        self.summary = plan.get("summary", {})
        self.dir = store.run_dir(self.id)
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._cancel = False

    # -------------------------------------------------------------- events
    def emit(self, event: dict) -> None:
        event.setdefault("ts", _now())
        event["jobId"] = self.id
        self.events.append(event)
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        if event["type"] == "log":
            store.append_run_log(self.id, f"{time.strftime('%H:%M:%S')} [{event.get('stepId','')}] {event['line']}")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subscribers.add(q)
        for ev in list(self.events)[-400:]:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                break
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "dryRun": self.dry_run,
            "created": self.created,
            "started": self.started,
            "ended": self.ended,
            "port": self.port,
            "summary": self.summary,
            "steps": self.steps,
            "plan": {k: v for k, v in self.plan.items() if k not in ("steps",)},
        }

    # -------------------------------------------------------------- control
    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def cancel(self) -> None:
        self._cancel = True
        proc = self._proc
        if proc and proc.returncode is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=4)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    # -------------------------------------------------------------- execution
    async def _await_port(self, step: dict) -> str | None:
        automatic = bool(self.plan.get("port", {}).get("automatic", True))
        fixed = self.plan.get("port", {}).get("resolved") or self.port
        port = detect.pick_port() if automatic else (
            fixed if (fixed and os.path.exists(fixed)) else detect.pick_port())
        if port:
            return port
        port = None
        # programmer 上传后设备会重新枚举，这里给一段宽限时间，避免"刚传完就说没串口"
        grace = int(step.get("portGrace") if step.get("portGrace") is not None
                    else self.plan.get("profile", {}).get("device", {}).get("portGrace", 20))
        if grace <= 0:
            return None
        deadline = _now() + grace
        announced = False
        while _now() < deadline and not self._cancel:
            if not announced:
                self.emit({"type": "log", "stepId": step["id"], "stream": "sys",
                           "line": f"等待端口 · 最长 {grace}s"})
                announced = True
            await asyncio.sleep(0.5)
            port = detect.pick_port() if automatic else (
                fixed if (fixed and os.path.exists(fixed)) else detect.pick_port())
            if port:
                self.emit({"type": "log", "stepId": step["id"], "stream": "sys",
                           "line": f"端口就绪 · {port}"})
                return port
        return None

    async def _run(self) -> None:
        try:
            await self._run_inner()
        except Exception as exc:  # noqa: BLE001 - surface any runner fault to the UI
            self.status = "failed"
            self.ended = _now()
            self.emit({"type": "log", "stepId": "", "stream": "err", "line": f"运行器异常 · {exc!r}"})
            for step in self.steps:
                if step["status"] in ("pending", "running"):
                    step["status"] = "fail" if step["status"] == "running" else "skipped"
            self.emit({"type": "job", "status": self.status, "steps": self.steps})

    async def _run_inner(self) -> None:
        self.started = _now()
        self.status = "running"
        self.emit({"type": "job", "status": "running", "dryRun": self.dry_run})
        store.save_run_meta(self.id, {
            "id": self.id, "status": "running", "created": self.created,
            "dryRun": self.dry_run, "summary": self.summary,
            "port": self.port, "profile": self.plan.get("profile", {}).get("name"),
        })

        needs_port = any("{PORT}" in a for st in self.steps for a in st.get("argv", []))
        if needs_port and not self.dry_run:
            manual = (self.plan.get("port") or {}).get("resolved") or ""
            port_now = manual if (manual and os.path.exists(manual)) else detect.pick_port()
            if not port_now:
                self.status = "failed"
                self.ended = _now()
                self.emit({"type": "log", "stepId": "", "stream": "err",
                           "line": "无可用端口 · 设备未进入 9008"})
                for st in self.steps:
                    st["status"] = "skipped"
                self.emit({"type": "job", "status": self.status, "steps": self.steps})
                store.save_run_meta(self.id, {"id": self.id, "status": self.status,
                                              "created": self.created, "ended": self.ended,
                                              "dryRun": self.dry_run, "summary": self.summary})
                return

        failed = False
        idx = 0
        while idx < len(self.steps):
            step = self.steps[idx]
            if self._cancel:
                step["status"] = "skipped"
                idx += 1
                continue

            if self.dry_run:
                step["status"] = "planned"
                step["started"] = _now()
                self.emit({"type": "step", "step": step})
                self.emit({"type": "log", "stepId": step["id"], "stream": "sys",
                           "line": "演练 · " + step["preview"]})
                step["ended"] = _now()
                idx += 1
                continue

            # 端口：只有命令里用到 {PORT} 才等待
            if any("{PORT}" in a for a in step["argv"]):
                port = await self._await_port(step)
                if not port:
                    step["status"] = "fail"
                    step["exitCode"] = None
                    self.emit({"type": "step", "step": step})
                    self.emit({"type": "log", "stepId": step["id"], "stream": "err",
                               "line": "无可用端口 · 步骤中止"})
                    failed = True
                    if not self.continue_on_error:
                        break
                    idx += 1
                    continue
            else:
                port = detect.pick_port() or ""

            for spec in step.get("files") or []:
                try:
                    (self.dir / str(spec.get("name"))).write_text(str(spec.get("content") or ""))
                except OSError as exc:
                    self.emit({"type": "log", "stepId": step["id"], "stream": "err",
                               "line": f"临时文件写入失败 · {exc}"})

            argv = [a.replace("{PORT}", port or "").replace("{RUNDIR}", str(self.dir))
                    for a in step["argv"]]
            step["port"] = port
            step["status"] = "running"
            step["started"] = _now()
            self.emit({"type": "step", "step": step})
            self.emit({"type": "log", "stepId": step["id"], "stream": "sys", "line": "$ " + " ".join(argv)})

            cwd = (step.get("cwd") or str(self.dir)).replace("{RUNDIR}", str(self.dir))
            env = dict(os.environ)
            env.update({"PYTHONUNBUFFERED": "1", "TERM": "dumb", "NO_COLOR": "1"})

            rc = None
            text_lines: list[str] = []
            timed_out = False
            if True:
                text_lines = []
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                except (OSError, ValueError) as exc:
                    step["status"] = "fail"
                    step["ended"] = _now()
                    self.emit({"type": "log", "stepId": step["id"], "stream": "err",
                               "line": f"启动失败 · {exc}"})
                    self.emit({"type": "step", "step": step})
                    failed = True
                    rc = None
                    break

                self._proc = proc
                timeout_s = float(step.get("timeout") or
                                  (120 if (step.get("tool") or "") == "QSaharaServer" else 300))
                timed_out = False

                async def _watchdog() -> None:
                    nonlocal timed_out
                    await asyncio.sleep(timeout_s)
                    timed_out = True
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

                watchdog = asyncio.create_task(_watchdog())
                buf = b""
                saw_error = False
                assert proc.stdout is not None
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    parts = LINE_SPLIT.split(buf)
                    buf = parts.pop()
                    for raw in parts:
                        line = _clean(raw)
                        if line:
                            text_lines.append(line)
                            if "ERROR:" in line:
                                saw_error = True
                            self.emit({"type": "log", "stepId": step["id"],
                                       "stream": "out", "line": line})
                if buf:
                    line = _clean(buf)
                    if line:
                        text_lines.append(line)
                        self.emit({"type": "log", "stepId": step["id"], "stream": "out", "line": line})

                rc = await proc.wait()
                watchdog.cancel()
                self._proc = None
                text = "\n".join(text_lines)
                if "Could not connect" in text:
                    self.emit({"type": "log", "stepId": step["id"], "stream": "err",
                               "line": "连接失败 · EDL 端口不可复用，请重新进入 9008"})
                    session.set_state(self.port, "absent", "端口不可用，需重新进入 9008")


            if rc is None:
                if not self.continue_on_error:
                    break
                idx += 1
                continue

            if timed_out:
                step["status"] = "fail"
                step["reason"] = "timeout"
                step["ended"] = _now()
                step["exitCode"] = rc
                self.emit({"type": "log", "stepId": step["id"], "stream": "err",
                           "line": f"超时 · {timeout_s:.0f}s，步骤已终止"})
                self.emit({"type": "step", "step": step})
                failed = True
                if not self.continue_on_error:
                    break
                idx += 1
                continue

            text = "\n".join(text_lines)
            transient = (
                "Could not connect" in text
                or "Unable to read packet header" in text
                or "Only read " in text
            )
            retry_count = int(step.get("_retry") or 0)
            if (rc != 0 and transient and step.get("key") == "sahara"
                    and retry_count < 5 and not self._cancel):
                retry_count += 1
                step["_retry"] = retry_count
                self.emit({"type": "log", "stepId": step["id"], "stream": "sys",
                           "line": f"重试 {retry_count}/5 · 端口未就绪或包被截断"})
                step["status"] = "pending"
                step.pop("exitCode", None)
                await asyncio.sleep(0.6)
                continue

            step["exitCode"] = rc
            step["ended"] = _now()
            step["status"] = "ok" if rc == 0 else "fail"
            self.emit({"type": "step", "step": step})
            self.emit({"type": "log", "stepId": step["id"],
                       "stream": "sys" if rc == 0 else "err",
                       "line": f"rc={rc} · {step['ended'] - step['started']:.1f}s"})

            if step["status"] == "ok":
                if step.get("key") == "sahara":
                    session.set_state(self.port, "programmer", "programmer 已上传")
                elif step.get("key") == "sha":
                    session.set_state(self.port, "authed", "授权链完成")

            if step["status"] != "ok":
                failed = True
                if not self.continue_on_error:
                    break

            if step.get("key") == "sahara" and step["status"] == "ok":
                # programmer 上传后设备重新枚举，等端口回来再继续（此时已进 Firehose，等待是安全的）
                waited = 0.0
                while waited < 15.0:
                    cand = detect.pick_port()
                    if cand and os.path.exists(cand):
                        self.port = cand
                        self.emit({"type": "log", "stepId": step["id"], "stream": "sys",
                                   "line": f"端口重新枚举 · {cand}"})
                        break
                    await asyncio.sleep(0.1)
                    waited += 0.1

            if step.get("rescan"):
                ports = detect.list_ports()
                new = detect.pick_port(ports)
                self.port = new or self.port
                self.emit({"type": "ports", "ports": ports, "resolved": new})

            idx += 1

        if self._cancel:
            self.status = "cancelled"
        elif failed:
            self.status = "failed"
        else:
            self.status = "done"
        for step in self.steps:
            if step["status"] == "pending":
                step["status"] = "skipped"
        self.ended = _now()
        try:
            arts = []
            for f in sorted(self.dir.iterdir()):
                if f.is_file() and f.suffix in (".img",) :
                    arts.append(f"{f.name} {f.stat().st_size / 1048576:.1f} MiB")
            self.emit({"type": "log", "stepId": "", "stream": "sys",
                       "line": f"产物目录 · {self.dir}"})
            if arts:
                self.emit({"type": "log", "stepId": "", "stream": "sys",
                           "line": "产物 · " + " ｜ ".join(arts)})
            self.emit({"type": "log", "stepId": "", "stream": "sys",
                       "line": f"日志 · {self.dir}/run.log"})
        except OSError:
            pass
        self.emit({"type": "job", "status": self.status, "steps": self.steps})
        store.save_run_meta(self.id, {
            "id": self.id, "status": self.status, "created": self.created,
            "ended": self.ended, "dryRun": self.dry_run, "summary": self.summary,
            "port": self.port, "profile": self.plan.get("profile", {}).get("name"),
        })

class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def create(self, plan: dict, dry_run: bool, continue_on_error: bool = False) -> Job:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        job = Job(job_id, plan, dry_run, continue_on_error)
        self.jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def all(self) -> list[dict]:
        return [{"id": j.id, "status": j.status, "created": j.created,
                 "summary": j.summary, "dryRun": j.dry_run}
                for j in sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)]


manager = JobManager()
