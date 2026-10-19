import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ModelSelection, ReasoningEffort } from "../api/types";
import { useMe } from "../lib/auth";
import { errorText } from "../lib/format";
import { Notice } from "./ui";
import { Icon } from "./Icon";

export const EFFORT_LABELS: Record<ReasoningEffort, string> = { none: "关闭", low: "低", high: "高", max: "最高" };

export function useModelSelection() {
  const me = useMe().data!;
  const key = `socialdog-model-${me.id}`;
  const catalog = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 60_000 });
  const [saved, setSaved] = useState<Partial<ModelSelection>>(() => {
    try { return JSON.parse(window.localStorage.getItem(key) ?? "null") ?? {}; } catch { return {}; }
  });
  const model = catalog.data?.models.find(m => m.id === saved.model)
    ?? catalog.data?.models.find(m => m.id === catalog.data.default_model) ?? catalog.data?.models[0];
  const effort = saved.reasoning_effort && model?.reasoning_efforts.includes(saved.reasoning_effort)
    ? saved.reasoning_effort : catalog.data?.default_reasoning_effort ?? "high";
  const selection: ModelSelection | undefined = model ? { model: model.id, reasoning_effort: effort } : undefined;
  function change(patch: Partial<ModelSelection>) {
    const value = { ...selection, ...patch };
    setSaved(value);
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* Selection still works without browser storage. */ }
  }
  return { catalog, selection, change };
}

function PickerMenu({ label, value, options, onChange, disabled }: {
  label: string; value: string; options: { value: string; label: string }[];
  onChange: (value: string) => void; disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const selected = options.find(option => option.value === value)?.label ?? value;

  function closeAndFocus() {
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  }

  useLayoutEffect(() => {
    if (!open || !trigger.current || !menu.current) return;
    const rect = trigger.current.getBoundingClientRect();
    const height = menu.current.offsetHeight;
    const width = menu.current.offsetWidth;
    const top = rect.top >= height + 16 ? rect.top - height - 8 : rect.bottom + 8;
    setPosition({ top: Math.max(8, Math.min(top, window.innerHeight - height - 8)), left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) });
    menu.current.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus({ preventScroll: true });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (!trigger.current?.contains(event.target as Node) && !menu.current?.contains(event.target as Node)) setOpen(false);
    }
    function dismiss() { setOpen(false); }
    function scroll(event: Event) { if (!menu.current?.contains(event.target as Node)) dismiss(); }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("scroll", scroll, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("scroll", scroll, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [open]);

  function onKey(event: KeyboardEvent) {
    const items = [...(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [])];
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape") { event.preventDefault(); closeAndFocus(); }
    if (event.key === "Tab") closeAndFocus();
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    }
  }

  return <>
    <button ref={trigger} id={id} type="button" className="model-trigger" aria-label={`${label}：${selected}`} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? `${id}-menu` : undefined} disabled={disabled} onClick={() => setOpen(!open)} onKeyDown={event => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); } }}>
      {label === "思考强度" && <span className="model-trigger-label">思考</span>}<span>{selected}</span><Icon name="chevron" size={13} className={open ? "-rotate-90" : "rotate-90"} />
    </button>
    {open && createPortal(<div ref={menu} id={`${id}-menu`} role="menu" aria-label={label} className="model-menu" style={position} onKeyDown={onKey}>
      <p className="model-menu-title">{label}</p>
      {options.map(option => <button type="button" role="menuitemradio" tabIndex={-1} aria-checked={value === option.value} className="model-menu-option" key={option.value} onClick={() => { onChange(option.value); closeAndFocus(); }}><span>{option.label}</span>{value === option.value && <Icon name="check" size={17} />}</button>)}
    </div>, document.body)}
  </>;
}

export function ModelPicker({ controls, disabled = false }: { controls: ReturnType<typeof useModelSelection>; disabled?: boolean }) {
  const { catalog, selection, change } = controls;
  if (catalog.error) return <Notice>{errorText(catalog.error)} <button type="button" className="underline" onClick={() => catalog.refetch()}>重试</button></Notice>;
  if (!selection) return <span role="status" className="text-sm text-muted">正在加载模型…</span>;
  const model = catalog.data!.models.find(m => m.id === selection.model)!;
  return <div className="model-controls">
    <PickerMenu label="模型" disabled={disabled} value={selection.model} options={catalog.data!.models.map(m => ({ value: m.id, label: m.label }))} onChange={model => change({ model })} />
    <PickerMenu label="思考强度" disabled={disabled} value={selection.reasoning_effort} options={model.reasoning_efforts.map(value => ({ value, label: EFFORT_LABELS[value] }))} onChange={reasoning_effort => change({ reasoning_effort: reasoning_effort as ReasoningEffort })} />
  </div>;
}
