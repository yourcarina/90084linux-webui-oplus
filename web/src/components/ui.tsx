import { useEffect, useRef, type ReactNode, type RefObject } from "react";

export function CopyBtn({ text, label = "复制" }: { text: string; label?: string }) {
  return (
    <button className="btn ghost sm" onClick={() => void navigator.clipboard?.writeText(text)}>
      {label}
    </button>
  );
}

export function Modal({
  title,
  children,
  onClose,
  foot,
  wide,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  foot?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className={`modal${wide ? " wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <span className="modal-title">{title}</span>
          <button className="btn ghost icon sm" onClick={onClose}>✕</button>
        </header>
        <div className="modal-body">{children}</div>
        {foot ? <footer className="modal-foot">{foot}</footer> : null}
      </div>
    </div>
  );
}

export function useAutoFocus<T extends HTMLElement>(dep?: unknown): RefObject<T | null> {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    ref.current?.focus();
  }, [dep]);
  return ref;
}
