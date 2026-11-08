import { useEffect, useMemo, useRef, useState } from "react";
import type { JobState, LogLine, Status } from "../types";
import { CopyBtn } from "./ui";

export type Entry =
  | ({ kind: "log" } & LogLine)
  | {
      kind: "sep";
      key: number;
      ts: number;
      stepId: string;
      title: string;
      tool: string;
      status: Status;
      preview: string;
      exitCode?: number | null;
      reason?: string;
    };

const STATUS_CLASS: Record<string, string> = {
  running: "run",
  ok: "ok",
  fail: "fail",
  planned: "run",
  skipped: "",
  pending: "",
};

function fmtTime(ts: number) {
  const d = new Date(ts);
  return d.toTimeString().slice(0, 8);
}

function elapsed(job: JobState | null) {
  if (!job?.started) return "—";
  const end = job.ended ?? Date.now() / 1000;
  const s = Math.max(0, Math.round(end - job.started));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function Console({
  entries,
  job,
  onClear,
  compact = false,
}: {
  entries: Entry[];
  job: JobState | null;
  onClear: () => void;
  compact?: boolean;
}) {
  const [autoscroll, setAutoscroll] = useState(true);
  const [verbose, setVerbose] = useState(false);
  const [query, setQuery] = useState("");
  const [tick, setTick] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!autoscroll) return;
    const el = bodyRef.current;
    if (!el) return;
    // 用 rAF 等 DOM 更新完再滚，避免新行还没渲染就滚动导致停在半路
    const id = requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    return () => cancelAnimationFrame(id);
  }, [entries, autoscroll]);

  const SUMMARY_KEYS = useMemo(
    () => ["产物", "详细日志", "sha256", "dump_", "超时", "会话", "串口已就绪",
           "Sahara", "跳过", "失败", "错误", "完成", "重试", "瞬时失败", "重新枚举"],
    [],
  );

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    let list = entries;
    if (!verbose) {
      list = entries.filter((e) => {
        if (e.kind === "sep") return true;
        if (e.stream === "err") return true;
        return SUMMARY_KEYS.some((k) => e.line.includes(k));
      });
    }
    if (!q) return list;
    return list.filter((e) =>
      e.kind === "log" ? e.line.toLowerCase().includes(q) : e.title.toLowerCase().includes(q));
  }, [entries, query, verbose, SUMMARY_KEYS]);

  const plain = useMemo(
    () =>
      entries
        .map((e) =>
          e.kind === "log"
            ? `${fmtTime(e.ts)} ${e.line}`
            : `\n▌ ${e.title} [${e.status}] ${e.preview}\n`,
        )
        .join("\n"),
    [entries],
  );

  const statusPill =
    job === null
      ? { cls: "", text: "idle" }
      : job.status === "failed"
        ? { cls: "err", text: "failed" }
        : job.status === "done"
          ? { cls: "ok", text: job.dryRun ? "dry-run ok" : "done" }
          : job.status === "cancelled"
            ? { cls: "warn", text: "cancelled" }
            : { cls: "blue", text: "running" };

  return (
    <section className="console">
      <div className={`console-head ${compact ? "compact" : ""}`}>
        {compact ? null : (
          <>
            <span className="console-title">Console</span>
            <span className={`pill ${statusPill.cls}`}>{statusPill.text}</span>
            <span className="console-title">T+{elapsed(job)}</span>
            <span style={{ flex: 1 }} />
          </>
        )}
        <input
          className="input"
          style={{ maxWidth: compact ? undefined : 150, height: 22, fontSize: 11 }}
          placeholder="过滤…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className={`btn ghost sm ${verbose ? "" : "dim"}`} onClick={() => setVerbose((v) => !v)}
                title="摘要只显示步骤、错误与产物；详细显示全部原始输出">
          {verbose ? "详细" : "摘要"}
        </button>
        <button className={`btn ghost sm ${autoscroll ? "" : "dim"}`} onClick={() => setAutoscroll((v) => !v)}
                title="跟随最新日志">
          {autoscroll ? "跟随" : "暂停"}
        </button>
        <CopyBtn text={plain} label="导出" />
        <button className="btn ghost sm" onClick={onClear} title="清空日志">清空</button>
      </div>
      <div className="ruler" />
      <div className="console-body" ref={bodyRef}>
        {!shown.length ? (
          <div className="console-empty">
            <div>
              NO SIGNAL
              <br />
              <span style={{ fontSize: 10 }}>
                载入物料 → 预览计划 → 开始 <span className="cursor" />
              </span>
            </div>
          </div>
        ) : (
          shown.map((e) =>
            e.kind === "sep" ? (
              <div key={e.key} className={`logsep ${STATUS_CLASS[e.status] ?? ""}`}>
                <span className="badge">
                  {e.status === "ok" ? "OK" : e.status === "fail" ? "FAIL"
                    : e.status === "skipped" ? "SKIP" : e.status === "pending" ? "···" : "RUN"}
                </span>
                <span>{e.title}</span>
                <span style={{ flex: 1 }} />
                {e.reason ? <span className="dim">{e.reason}</span> : null}
                {typeof e.exitCode === "number" ? <span className="dim">rc={e.exitCode}</span> : null}
              </div>
            ) : (
              <div key={e.key} className={`logline ${e.stream}`}>
                <span className="t">{fmtTime(e.ts)}</span>
                <span className="g">{e.stream === "err" ? "✗" : e.stream === "sys" ? "›" : "·"}</span>
                <span className="m">{e.line}</span>
              </div>
            ),
          )
        )}
      </div>
      {compact ? null : <div className="console-foot">
        <span>{shown.filter((e) => e.kind === "log").length} / {entries.filter((e) => e.kind === "log").length} 行</span>
        <span className="sep">·</span>
        <span>{job ? `job ${job.id}` : "no job"}</span>
        <span className="sep">·</span>
        <span>{job?.dryRun ? "dry-run（未执行）" : "live"}</span>
        <span style={{ flex: 1 }} />
        <span>{job?.port ?? "—"}</span>
        <span className="sep">·</span>
        <span>t{tick}</span>
      </div>}
    </section>
  );
}
