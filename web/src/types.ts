export type Status = "pending" | "running" | "ok" | "fail" | "skipped" | "planned";

export interface Port {
  path: string;
  name: string;
  exists: boolean;
  writable: boolean;
  vid: string;
  pid: string;
  product: string;
  mode: string;
  is_edl: boolean;
}

export interface Env {
  root: string;
  platform: string;
  dataDir: string;
  tools: { qsahara: { version: string }; fh_loader: { version: string } };
  ports: Port[];
  permissions: { udev_installed: boolean };
}

export interface StepInfo {
  id: string;
  key: string;
  title: string;
  tool: string;
  preview: string;
  status: Status;
  exitCode?: number | null;
  reason?: string;
}

export interface LogLine {
  key: number;
  ts: number;
  stepId: string;
  stream: "out" | "err" | "sys";
  line: string;
}

export interface JobState {
  id: string;
  status: "starting" | "running" | "done" | "failed" | "cancelled";
  dryRun: boolean;
  started: number | null;
  ended: number | null;
  port: string | null;
  steps: StepInfo[];
  summary?: { steps: number; images: number; bytes: number; human: string; errors: number; warnings: number };
}

export type JobEvent =
  | { type: "log"; stepId: string; stream: LogLine["stream"]; line: string; ts: number }
  | { type: "step"; step: StepInfo; ts: number }
  | { type: "job"; status: JobState["status"]; steps?: StepInfo[]; ts: number }
  | { type: "ports"; ports: Port[]; resolved: string | null; ts: number };

export interface FsEntry {
  name: string;
  path: string;
  dir: boolean;
  size: number;
  mtime: number;
  ext: string;
}

export interface FsListing {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  roots: { name: string; path: string }[];
}
