import { useEffect, useMemo, useState } from "react";
import type { FsEntry, FsListing } from "../types";
import { Modal } from "./ui";

function human(size: number) {
  if (!size) return "—";
  const units = ["B", "K", "M", "G", "T"];
  let v = size;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)}${units[i]}`;
}

export function FileBrowser({
  mode,
  title,
  filter,
  initial,
  onClose,
  onSelect,
}: {
  mode: "file" | "dir";
  title: string;
  filter?: string[];
  initial?: string;
  onClose: () => void;
  onSelect: (path: string) => void;
}) {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState<FsEntry | null>(null);
  const [path, setPath] = useState(initial || "");

  const load = async (target: string) => {
    setErr(null);
    try {
      const res = await fetch("/api/fs/list", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: target }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || "读取失败");
      const data = (await res.json()) as FsListing;
      setListing(data);
      setPath(data.path);
      setSel(null);
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  };

  useEffect(() => {
    void load(initial || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const entries = useMemo(() => {
    const items = listing?.entries ?? [];
    const q = query.trim().toLowerCase();
    const filtered = q ? items.filter((e) => e.name.toLowerCase().includes(q)) : items;
    if (mode === "dir") return filtered;
    const exts = filter?.length ? filter : null;
    return filtered.filter((e) => e.dir || !exts || exts.includes(e.ext) || exts.includes("*"));
  }, [listing, query, mode, filter]);

  const open = (entry: FsEntry) => {
    if (entry.dir) {
      void load(entry.path);
    } else {
      setSel(entry);
    }
  };

  const commit = () => {
    if (mode === "dir") {
      onSelect(path);
    } else if (sel) {
      onSelect(sel.path);
    }
  };

  return (
    <Modal
      title={title}
      wide
      onClose={onClose}
      foot={
        <>
          <span className="label" style={{ marginRight: "auto" }}>
            {mode === "dir" ? "选择当前目录" : sel ? sel.path : "选择文件"}
          </span>
          <button className="btn sm" onClick={onClose}>
            取消
          </button>
          <button
            className="btn primary sm"
            disabled={mode === "file" && !sel}
            onClick={commit}
          >
            选定
          </button>
        </>
      }
    >
      <div className="fs-roots">
        {(listing?.roots ?? []).map((r) => (
          <button key={r.path} className="btn ghost sm" onClick={() => void load(r.path)}>
            {r.name}
          </button>
        ))}
        <button
          className="btn ghost sm"
          disabled={!listing?.parent}
          onClick={() => listing?.parent && void load(listing.parent)}
        >
          ↑ 上级
        </button>
        <input
          className="input"
          style={{ maxWidth: 240, marginLeft: "auto" }}
          placeholder="过滤文件名…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className="fs-crumbs">
        <span className="dim">PATH</span>
        <span>{path}</span>
      </div>
      {err ? <div className="warn-item error">{err}</div> : null}
      <div className="fs-list">
        {entries.map((e) => (
          <div
            key={e.path}
            className={`fs-entry ${e.dir ? "dir" : ""} ${sel?.path === e.path ? "sel" : ""}`}
            onClick={() => (e.dir ? void open(e) : setSel(e))}
            onDoubleClick={() => (e.dir ? void open(e) : commit())}
          >
            <span className="ico">{e.dir ? "▸" : "·"}</span>
            <span>{e.name}</span>
            <span className="sz">{e.dir ? "DIR" : human(e.size)}</span>
          </div>
        ))}
        {!entries.length && !err ? (
          <div style={{ padding: 14 }} className="dim mono">
            空目录 / 无匹配
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
