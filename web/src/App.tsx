import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Env, JobEvent, JobState, Port, Status } from "./types";
import { Console } from "./components/Console";
import type { Entry } from "./components/Console";
import { FileBrowser } from "./components/FileBrowser";
import "./styles.css";

type Part = { label: string; lun: number; start: number; sectors: number; size: number; human: string; present: boolean; path: string; file: string };

type Toast = { id: number; text: string; err?: boolean };

type ManualRow = {
  id: number;
  label: string;
  lun: string;
  start: string;
  sectors: string;
  image: string;
};

const mLen = (v: string) => v.trim().length > 0;
const digits = (v: string) => v.replace(/[^0-9]/g, "");

function validateRow(r: ManualRow, mode: "read" | "write"): Record<string, string> {
  const e: Record<string, string> = {};
  if (!/^[A-Za-z0-9_.\-]{1,36}$/.test(r.label.trim())) e.label = "label 只能含字母数字 _ . -，长度 1–36";
  const lun = Number(r.lun);
  if (!mLen(r.lun) || !Number.isInteger(lun) || lun < 0 || lun > 31) e.lun = "LUN 需为 0–31 的整数";
  if (!mLen(r.start) || Number(r.start) < 0) e.start = "start 需为不小于 0 的整数";
  const sec = Number(r.sectors);
  if (!mLen(r.sectors) || sec <= 0) e.sectors = "sectors 需为大于 0 的整数";
  else if (sec % 8 !== 0) e.sectors = "sectors 应为 8 的倍数（4K 边界）";
  if (mode === "write" && !r.image.trim()) e.image = "写入需要指定镜像文件";
  return e;
}

export default function App() {
  const [env, setEnv] = useState<Env | null>(null);
  const [ports, setPorts] = useState<Port[]>([]);
  const [port, setPort] = useState("auto");

  const [dir, setDir] = useState("");          // 授权物料目录
  const [fwDir, setFwDir] = useState("");      // 9008 固件包目录
  const [programmer, setProgrammer] = useState("");
  const [digest, setDigest] = useState("");
  const [sign, setSign] = useState("");
  const [parts, setParts] = useState<Part[]>([]);
  const [building, setBuilding] = useState(false);
  const [pkg, setPkg] = useState<{
    model?: string; version?: string; count?: number; withImage?: number;
    human?: string; missing?: string[]; optionalMissing?: string[];
    unreferenced?: { name: string; human: string }[];
  } | null>(null);
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [manualRows, setManualRows] = useState<ManualRow[]>([]);
  const [mode, setMode] = useState<"read" | "write">("read");
  const [chunk, setChunk] = useState(8192);
  const [extraArgs, setExtraArgs] = useState("");
  const [firehoseOk, setFirehoseOk] = useState(false);

  const [entries, setEntries] = useState<Entry[]>([]);
  const [jobInfo, setJobInfo] = useState<{ id: string; status: JobState["status"]; dryRun: boolean; started: number | null; ended: number | null; port: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [browser, setBrowser] = useState<{ kind: "file" | "dir"; title: string; filter?: string[]; apply: (p: string) => void } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [sess, setSess] = useState<{ state: string; note: string; hint?: string } | null>(null);
  const [probing, setProbing] = useState(false);
  const [drawerH, setDrawerH] = useState(300);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);
  const [showMaterial, setShowMaterial] = useState(true);

  const keyRef = useRef(0);
  const sepKeyRef = useRef<Map<string, number>>(new Map());
  const esRef = useRef<EventSource | null>(null);

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startY: e.clientY, startH: drawerH };
    const onMove = (ev: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const next = d.startH + (d.startY - ev.clientY);
      setDrawerH(Math.min(Math.max(120, next), window.innerHeight - 140));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      dragRef.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const toast = useCallback((text: string, err = false) => {
    const id = ++keyRef.current;
    setToasts((t) => [...t, { id, text, err }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const r = await fetch("/api/env");
        const d = (await r.json()) as Env;
        setEnv(d); setPorts(d.ports);
      } catch { /* ignore */ }
    })();
    const id = window.setInterval(() => {
      void fetch("/api/ports").then((r) => r.json()).then((d) => setPorts(d.ports)).catch(() => undefined);
    }, 1200);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const st = sess?.state;
    if (st === "authed" || st === "programmer") setFirehoseOk(true);
    if (st === "edl" || st === "dirty" || st === "absent" || st === "busy") setFirehoseOk(false);
  }, [sess?.state]);

  const [restarting, setRestarting] = useState(false);

  const cancelJob = async () => {
    if (!jobInfo) return;
    try {
      await fetch(`/api/runs/${jobInfo.id}/cancel`, { method: "POST" });
      toast("已请求取消");
    } catch (e) { toast((e as Error).message, true); }
  };

  const resetDevice = async () => {
    try {
      const r = await fetch("/api/device/reset", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: port === "auto" ? "" : port }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d?.detail || "复位失败");
      toast(d.ok ? "已下发复位，设备正在重启" : "复位命令未确认", !d.ok);
    } catch (e) { toast((e as Error).message, true); }
  };

  const restartServer = async () => {
    setRestarting(true);
    toast("正在杀掉所有进程并重启…");
    try { await fetch("/api/admin/restart", { method: "POST" }); } catch { /* 连接会被切断 */ }
    // 轮询直到服务回来
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => window.setTimeout(r, 500));
      try {
        const r = await fetch("/api/env", { cache: "no-store" });
        if (r.ok) {
          toast("已重启");
          setRestarting(false);
          setEntries([]);
          window.location.reload();
          return;
        }
      } catch { /* 还没起来 */ }
    }
    setRestarting(false);
    toast("重启超时，请检查后端进程", true);
  };

  const loadSession = useCallback(async () => {
    try {
      const r = await fetch("/api/session");
      setSess(await r.json());
    } catch { /* ignore */ }
  }, []);


  useEffect(() => {
    void loadSession();
    const t = window.setInterval(() => void loadSession(), 5000);
    return () => window.clearInterval(t);
  }, [loadSession]);


  // 刷新后恢复后台任务
  useEffect(() => {
    void (async () => {
      try {
        const r = await fetch("/api/jobs/active");
        const d = await r.json();
        if (!d.active) return;
        const snap = d.active as { id: string; status: JobState["status"]; started: number | null; port: string | null; events: JobEvent[] };
        const ones: Entry[] = [];
        for (const e of snap.events || []) {
          if (e.type === "log") ones.push({ kind: "log", key: ++keyRef.current, ts: e.ts, stepId: e.stepId, stream: e.stream, line: e.line });
          else if (e.type === "step" && e.step.status === "running") ones.push({ kind: "sep", key: ++keyRef.current, ts: e.ts, stepId: e.step.id, title: e.step.title, tool: e.step.tool, status: "running" as Status, preview: e.step.preview });
        }
        setEntries(ones);
        setJobInfo({ id: snap.id, status: snap.status, dryRun: false, started: snap.started, ended: null, port: snap.port });
        setBusy(snap.status === "running");
        if (snap.status === "running") attachStream(snap.id);
      } catch { /* ignore */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadPackage = async (target: string): Promise<boolean> => {
    try {
      const r = await fetch("/api/package/scan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dir: target }),
      });
      const d = await r.json();
      if (!r.ok || !d.count) return false;
      const rows: Part[] = (d.partitions || []).map((p: Record<string, unknown>) => ({
        label: String(p.label), lun: Number(p.lun), start: Number(p.start), sectors: Number(p.sectors),
        size: Number(p.size), human: String(p.human), present: Boolean(p.present),
        path: String(p.image || ""), file: String(p.imageName || ""),
      }));
      setParts(rows);
      setSel(Object.fromEntries(rows.filter((p) => p.present).map((p) => [`${p.label}@${p.lun}`, true])));
      setPkg({
        model: d.model, version: d.version, count: d.count, withImage: d.withImage,
        human: d.human, missing: d.missing || [], optionalMissing: d.optionalMissing || [],
        unreferenced: d.unreferenced || [],
      });
      setMsg(`已解析：${d.count} 个分区，${d.withImage} 个可用镜像，${d.human}`);
      return true;
    } catch {
      return false;
    }
  };

  const loadPackageDir = async (target: string) => {
    setFwDir(target);
    const ok = await loadPackage(target);
    if (!ok) { setParts([]); setPkg(null); toast("未识别为 9008 固件包", true); }
  };

  const buildSuper = async () => {
    if (!fwDir) return;
    setBuilding(true);
    setMsg("正在合成 super.img…（反稀疏 + lpmake，可能需要数分钟）");
    try {
      const r = await fetch("/api/package/super", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dir: fwDir }),
      });
      const d = await r.json();
      if (!r.ok || !d.ok) { toast(d.error || "super 合成失败", true); setMsg(null); return; }
      toast(`已合成 super.img · ${d.human}`);
      await loadPackageDir(fwDir);
    } catch (e) {
      toast((e as Error).message, true);
      setMsg(null);
    } finally {
      setBuilding(false);
    }
  };

  const autoMatchFiles = async (target: string) => {
    if (!target) return;
    try {
      const r = await fetch("/api/firmware/auto", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dir: target }) });
      const d = await r.json();
      setProgrammer(d.programmer || "");
      setDigest(d.digest || "");
      setSign(d.sign || "");

    } catch (e) { toast((e as Error).message, true); }
  };

  const autoMatch = async (target = dir) => {
    if (!target) { toast("先选目录", true); return; }
    setMsg(null);
    await autoMatchFiles(target);
  };

  const addRow = () =>
    setManualRows((rows) => [
      ...rows,
      { id: Date.now() + rows.length, label: "", lun: "4", start: "", sectors: "", image: "" },
    ]);

  const updateRow = (id: number, patch: Partial<ManualRow>) =>
    setManualRows((rows) => rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  const rowErrors = useMemo(
    () => manualRows.map((r) => validateRow(r, mode)),
    [manualRows, mode],
  );
  const manualErrorCount = rowErrors.reduce((a, e) => a + Object.keys(e).length, 0);

  const chosen = useMemo(() => parts.filter((p) => sel[`${p.label}@${p.lun}`]), [parts, sel]);
  const totalBytes = chosen.reduce((a, p) => a + p.size, 0);

  const attachStream = useCallback((jobId: string, phase?: "firehose" | "read" | "write") => {
    const es = new EventSource(`/api/runs/${jobId}/events`);
    esRef.current?.close();
    esRef.current = es;
    const terminal = new Set(["done", "failed", "cancelled"]);
    es.onmessage = (msg) => {
      let e: JobEvent;
      try { e = JSON.parse(msg.data) as JobEvent; } catch { return; }
      if (e.type === "log") {
        setEntries((prev) => [...prev.slice(-3000),
          { kind: "log", key: ++keyRef.current, ts: e.ts, stepId: e.stepId, stream: e.stream, line: e.line }]);
      } else if (e.type === "step") {
        const st = e.step.status;
        setEntries((prev) => {
          const known = sepKeyRef.current.get(e.step.id);
          if (st === "running" || st === "pending") {
            if (known !== undefined && prev.some((x) => x.key === known)) return prev;
            const key = ++keyRef.current;
            sepKeyRef.current.set(e.step.id, key);
            return [...prev, { kind: "sep", key, ts: e.ts, stepId: e.step.id,
              title: e.step.title, tool: e.step.tool, status: st as Status, preview: e.step.preview }];
          }
          // 结束态：更新对应的那一行，而不是新加一条
          if (known !== undefined) {
            return prev.map((x) => x.key === known
              ? { ...x, status: st as Status, exitCode: e.step.exitCode, reason: e.step.reason }
              : x);
          }
          return [...prev, { kind: "sep", key: ++keyRef.current, ts: e.ts, stepId: e.step.id,
            title: e.step.title, tool: e.step.tool, status: st as Status,
            exitCode: e.step.exitCode, preview: e.step.preview }];
        });
      } else if (e.type === "job") {
        setJobInfo((j) => j ? { ...j, status: e.status,
          ended: terminal.has(e.status) ? Date.now() / 1000 : j.ended } : j);
        if (terminal.has(e.status)) {
          es.close(); esRef.current = null; setBusy(false);
          if (e.status === "done") {
            if (phase === "firehose" || !phase) setFirehoseOk(true);
            toast(phase === "firehose" ? "已进入 Firehose" : "完成");
          } else {
            toast("中断", true);
          }
        }
      }
    };
  }, [toast]);

  const runPhase = async (phase: "firehose" | "read" | "write") => {
    const manualSpec = manualRows.map((r) => ({
      label: r.label.trim(),
      lun: Number(r.lun),
      start: Number(r.start),
      sectors: Number(r.sectors),
      path: phase === "write" ? r.image.trim() : "",
      file: "",
    }));
    const specParts = phase === "firehose"
      ? []
      : [
          ...chosen.map((p) => ({ label: p.label, lun: p.lun, start: p.start,
                                  sectors: p.sectors, path: p.path, file: p.file })),
          ...manualSpec,
        ];
    if (phase !== "firehose" && !specParts.length) { toast("先选分区或手动添加一个", true); return; }
    if (phase !== "firehose" && manualErrorCount > 0) {
      const msgs = rowErrors.flatMap((e, i) =>
        Object.entries(e).map(([k, v]) => `#${i + 1} ${k}: ${v}`));
      toast("手动分区参数不合法：" + msgs.join("；"), true);
      return;
    }
    setBusy(true);
    sepKeyRef.current.clear();
    setEntries((prev) => [...prev, {
      kind: "sep", key: ++keyRef.current, ts: Date.now() / 1000, stepId: "",
      title: phase === "firehose" ? "进入 FIREHOSE" : phase === "write" ? "开始写入" : "开始提取",
      tool: "", status: "pending" as Status,
    } as Entry]);
    try {
      const r = await fetch("/api/flow/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec: {
          programmer, digest, sign, phase,
          mode: phase === "firehose" ? "read" : phase,
          parts: specParts, chunkSectors: chunk,
          extraArgs: extraArgs.split(/\s+/).filter(Boolean),
          skipAuth: firehoseOk && phase !== "firehose",
          port: port === "auto" ? "" : port } }),
      });
      const d = await r.json();
      if (d?.already) {
        setFirehoseOk(true);
        toast(d.message || "已授权");
        setBusy(false);
        return;
      }
      if (!r.ok) {
        const detail = d?.detail;
        const text = typeof detail === "string" ? detail
          : (detail?.message || detail?.errors?.join("；") || "启动失败");
        if (r.status === 409) {
          setSess({ state: detail?.dirty ? "dirty" : "busy", note: text, hint: detail?.hint });
          toast(text + (detail?.hint ? `；${detail.hint}` : ""), true);
        } else {
          toast(text, true);
        }
        setBusy(false);
        return;
      }
      setJobInfo({ id: d.id, status: "running", dryRun: false, started: Date.now() / 1000, ended: null, port: port });
      attachStream(d.id, phase);
    } catch (e) { toast((e as Error).message, true); setBusy(false); }
  };

  const portStatus = (() => {
    const st = sess?.state;
    if (!ports.length) return "无设备";
    if (st === "busy") return "端口被占用";
    if (st === "dirty") return "会话失效 · 需冷启动";
    if (st === "authed") return "9008 · 已授权";
    if (st === "programmer") return "9008 · 已握手";
    if (st === "firehose") return "9008 · Firehose";
    if (ports.some((p) => p.is_edl)) return "9008 在线 · 待握手";
    return "串口在线";
  })();
  const portBad = sess?.state === "dirty" || sess?.state === "busy";
  const job: JobState | null = jobInfo
    ? { id: jobInfo.id, status: jobInfo.status, dryRun: jobInfo.dryRun, started: jobInfo.started, ended: jobInfo.ended, port: jobInfo.port, steps: [], summary: { steps: 0, images: 0, bytes: 0, human: "0 B", errors: 0, warnings: 0 } }
    : null;

  return (
    <div className="app">
      <div className="shell">
        {/* ---------------------------------------------------------- toolbar */}
        <header className="bar">
          <span className="brand">EDL Bench</span>
          <span className="brand-sub">signed 9008</span>
          <span className="grow" />
          {jobInfo?.status === "running" ? (
            <>
              <span className="running-tag">{jobInfo.id}</span>
              <button className="btn ghost sm" onClick={() => void cancelJob()}>取消</button>
            </>
          ) : null}
        </header>

        <div className="main">
          {/* -------------------------------------------------------- left */}
          <aside className="pane left">
            <div className="stack">
            <div className="group-head">设备配置</div>
            <div className="rowgroup">
              <div className="device-line">
                <span className={`dot ${ports.length ? (portBad ? "err" : "on") : ""}`} />
                <span className="path">{ports.find((p) => p.is_edl)?.path || ports[0]?.path || "—"}</span>
                {ports.length ? (
                  <button className="btn ghost sm" style={{ marginLeft: "auto" }}
                          onClick={() => void resetDevice()}>复位</button>
                ) : null}
              </div>
              {ports.length ? (
                <div className="device-sub">
                  {portStatus}
                  {(() => {
                    const p = ports.find((x) => x.is_edl) || ports[0];
                    const sn = p?.product?.match(/SN:([0-9A-Za-z]+)/)?.[1];
                    return sn ? ` · SN ${sn}` : "";
                  })()}
                </div>
              ) : null}
            </div>

            <div className="rowgroup">
              <div className="mat-dir">
                <input className="input mono" value={dir} placeholder="授权物料目录…"
                       onChange={(e) => setDir(e.target.value)}
                       onBlur={() => { if (dir) void autoMatch(dir); }}
                       onKeyDown={(e) => { if (e.key === "Enter" && dir) void autoMatch(dir); }} />
                <button className="btn ghost sm"
                        onClick={() => setBrowser({ kind: "dir", title: "选择授权物料目录",
                                                   apply: (v) => { setDir(v); void autoMatch(v); } })}>浏览</button>
              </div>
              {dir ? (
              <div className="files" style={{ marginTop: 8 }}>
                {([
                  ["MELF", programmer, setProgrammer, ["*.melf", "*.elf", "*.mbn"]],
                  ["DIGEST", digest, setDigest, ["*.elf", "*.mbn", "*.bin"]],
                  ["SIGN", sign, setSign, ["*.bin"]],
                ] as const).map(([label, value, setter, filter]) => (
                  <button key={label} className={`file-row ${value ? "set" : ""}`}
                          title={value || "未选择"}
                          onClick={() => setBrowser({
                            kind: "file", title: `选择 ${label}`, filter: [...filter], apply: setter,
                          })}>
                    <span className="fk">{label}</span>
                    <span className="fv">{value ? value.split("/").pop() : "选择文件…"}</span>
                  </button>
                ))}
              </div>
              ) : null}
              {dir ? (
                <div style={{ marginTop: 10 }}>
                  {firehoseOk ? (
                    <span className="ready-tag">Firehose 就绪</span>
                  ) : (
                    <button className="btn primary" style={{ width: "100%" }}
                            disabled={busy || !programmer || !digest || !sign || !ports.length}
                            onClick={() => void runPhase("firehose")}>进入 FIREHOSE</button>
                  )}
                </div>
              ) : null}
            </div>

            {/* 9008 固件包 */}
            <div className="rowgroup">
              <div className="mat-dir">
                <input className="input mono" value={fwDir} placeholder="9008 固件包目录…"
                       onChange={(e) => setFwDir(e.target.value)}
                       onBlur={() => { if (fwDir) void loadPackageDir(fwDir); }}
                       onKeyDown={(e) => { if (e.key === "Enter" && fwDir) void loadPackageDir(fwDir); }} />
                <button className="btn ghost sm"
                        onClick={() => setBrowser({ kind: "dir", title: "选择 9008 固件包目录",
                                                   apply: (v) => { setFwDir(v); void loadPackageDir(v); } })}>浏览</button>
              </div>
              {pkg ? (
                <>
                  <div className="device-sub" style={{ marginTop: 6 }}>
                    {pkg.count} 个分区 · 可用镜像 {pkg.withImage} 个 · 写入量 {pkg.human}
                  </div>
                  {pkg.version ? <div className="device-sub">{pkg.version}</div> : null}
                  {pkg.missing && pkg.missing.length ? (
                    <div className="device-warn">缺失镜像：{pkg.missing.join("、")}</div>
                  ) : null}
                  {pkg.optionalMissing && pkg.optionalMissing.length ? (
                    <div className="device-note">可选缺失：{pkg.optionalMissing.join("、")}</div>
                  ) : null}
                  {pkg.unreferenced && pkg.unreferenced.length ? (
                    <>
                      <div className="device-note">
                        另有 {pkg.unreferenced.length} 个动态分区镜像未被 rawprogram 引用（如{" "}
                        {pkg.unreferenced[0].name} {pkg.unreferenced[0].human}）
                      </div>
                      {parts.some((p) => p.label === "super" && p.present) ? null : (
                        <button className="btn ghost sm" style={{ marginTop: 6 }}
                                disabled={building}
                                title="按 META/super_def 用 lpmake 把动态分区组装成 super.img"
                                onClick={() => void buildSuper()}>
                          {building ? "合成中…" : "合成 super.img"}
                        </button>
                      )}
                    </>
                  ) : null}
                </>
              ) : fwDir ? (
                <div className="device-sub" style={{ marginTop: 6 }}>未识别为 9008 固件包</div>
              ) : null}
            </div>

            <div className="rowgroup">
              <div className="opt-row">
                <span className="opt-key">分块</span>
                <select className="select" value={chunk} onChange={(e) => setChunk(Number(e.target.value))}>
                  <option value={2048}>8 MiB</option>
                  <option value={4096}>16 MiB</option>
                  <option value={8192}>32 MiB</option>
                  <option value={16384}>64 MiB</option>
                </select>
              </div>
              <div className="opt-row">
                <span className="opt-key">附加参数</span>
                <input className="input mono" value={extraArgs} spellCheck={false}
                       placeholder="--verify_programming"
                       onChange={(e) => setExtraArgs(e.target.value)} />
              </div>
              {msg ? <div className="opt-msg">{msg}</div> : null}
            </div>

            </div>

            <div className="logbox">
              <div className="group-head">
                <span>日志</span>
                <span className="head-meta">
                  {entries.filter((e) => e.kind === "log").length} 行
                </span>
                <span className="grow" />
                <span className="head-meta mono-sm">{env?.tools.fh_loader.version || ""}</span>
              </div>
              <div className="drawer-body">
                <Console entries={entries} job={job} onClear={() => setEntries([])} compact />
              </div>
            </div>
          </aside>

          {/* ------------------------------------------------------- right */}
          <main className="pane right">

            <div className="sheet-scroll">
              <div className="row-head" style={{ flex: "none" }}>
                <span /><span>分区</span><span>LUN</span><span>起始</span><span>扇区</span><span>大小</span><span>镜像</span><span />
              </div>

              {parts.map((p) => {
                const k = `${p.label}@${p.lun}`;
                const blocked = mode === "write" && !p.present;
                const on = !!sel[k] && !blocked;
                return (
                  <div key={k} className={`row-item ${on ? "on" : ""} ${blocked ? "blocked" : ""}`}
                       title={p.path || "包内没有该分区的镜像"}
                       onClick={() => { if (!blocked) setSel((st) => ({ ...st, [k]: !on })); }}>
                    <span className={`box ${on ? "on" : ""}`}>{on ? "✓" : ""}</span>
                    <span className="c-name">{p.label}</span>
                    <span className="c-dim">{p.lun}</span>
                    <span className="c-dim">{p.start}</span>
                    <span className="c-dim">{p.sectors}</span>
                    <span className="c-size">{p.human}</span>
                    <span className={p.present ? "c-img" : "c-warn"}>
                      {p.present ? p.file || "有镜像" : "缺文件"}
                    </span>
                    <span />
                  </div>
                );
              })}

              {manualRows.map((r, i) => {
                const e = rowErrors[i] || {};
                return (
                  <div key={r.id} className="row-item manual">
                    <span className="box manual-mark">M</span>
                    <input className={`cell ${e.label ? "bad" : ""}`} placeholder="label"
                           value={r.label} onChange={(ev) => updateRow(r.id, { label: ev.target.value })} />
                    <input className={`cell num ${e.lun ? "bad" : ""}`} inputMode="numeric" placeholder="LUN"
                           value={r.lun} onChange={(ev) => updateRow(r.id, { lun: digits(ev.target.value) })} />
                    <input className={`cell num ${e.start ? "bad" : ""}`} inputMode="numeric" placeholder="起始"
                           value={r.start} onChange={(ev) => updateRow(r.id, { start: digits(ev.target.value) })} />
                    <input className={`cell num ${e.sectors ? "bad" : ""}`} inputMode="numeric" placeholder="扇区"
                           value={r.sectors} onChange={(ev) => updateRow(r.id, { sectors: digits(ev.target.value) })} />
                    <span className="c-size">{mLen(r.sectors) ? `${((Number(r.sectors) * 4096) / 1048576).toFixed(1)} MiB` : "—"}</span>
                    {mode === "write" ? (
                      <button className={`file-chip mini ${r.image ? "set" : e.image ? "bad" : ""}`}
                              onClick={() => setBrowser({
                                kind: "file", title: "选择镜像", filter: [".img", ".bin"],
                                apply: (v) => updateRow(r.id, { image: v }),
                              })}>
                        {r.image ? r.image.split("/").pop() : "选镜像"}
                      </button>
                    ) : <span className="c-dim">—</span>}
                    <button className="row-x" title="删除该行"
                            onClick={() => setManualRows((rows) => rows.filter((x) => x.id !== r.id))}>✕</button>
                  </div>
                );
              })}

              {parts.length === 0 && manualRows.length === 0 ? (
                <div className="sheet-empty">
                  <div className="se-title">分区列表为空</div>
                  <div className="se-sub">选择 9008 固件包后自动解析，或手动新增分区</div>
                </div>
              ) : null}

              </div>

            {manualErrorCount > 0 ? (
              <div className="sheet-foot warn">
                手动分区 {manualErrorCount} 处不合法 · {(() => {
                  const first = rowErrors.findIndex((e) => Object.keys(e).length);
                  const [k, v] = Object.entries(rowErrors[first] || {})[0] || [];
                  return `#${first + 1} ${k} ${v}`;
                })()}
              </div>
            ) : null}
              <div className="sheet-bar">
              {parts.length === 0 ? (
                <button className="btn ghost sm" onClick={addRow}>新增分区</button>
              ) : null}
              <span className="sheet-sub">
                {parts.length} 分区 · 已选 {chosen.length}
                {mode === "write" ? ` · ${(chosen.reduce((a, p) => a + p.size, 0) / 1073741824).toFixed(2)} GiB` : ""}
              </span>
              <span className="divider" />
              <button className="btn ghost sm"
                      title={mode === "write" ? "只选有镜像的分区" : "全选"}
                      onClick={() => setSel(Object.fromEntries(
                        parts.filter((p) => mode !== "write" || p.present)
                             .map((p) => [`${p.label}@${p.lun}`, true])))}>全选</button>
              <button className="btn ghost sm" onClick={() => setSel({})}>清空</button>
              <span className="grow" />
              <div className="seg">
                <button className={mode === "read" ? "active" : ""} onClick={() => setMode("read")}>提取</button>
                <button className={mode === "write" ? "active" : ""} onClick={() => setMode("write")}>写入</button>
              </div>
              <button className={`btn sm ${mode === "write" ? "armed" : "primary"}`}
                      disabled={busy || !firehoseOk} onClick={() => void runPhase(mode)}>
                {mode === "write" ? "开始写入" : "开始提取"}
              </button>
              </div>
          </main>
        </div>

        {/* --------------------------------------------------------- drawer */}
      </div>

      {browser ? (
        <FileBrowser mode={browser.kind} title={browser.title} filter={browser.filter}
          initial={browser.kind === "dir" ? dir : undefined}
          onClose={() => setBrowser(null)}
          onSelect={(p) => { browser.apply(p); setBrowser(null); if (browser.kind === "dir") void autoMatch(p); }} />
      ) : null}

      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.err ? "err" : ""}`}>{t.text}</div>)}
      </div>
    </div>
  );
}
