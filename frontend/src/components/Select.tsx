import { Children, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icon";

export interface Choice { value: string; label: string; disabled?: boolean }
type TriggerProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange" | "value" | "children" | "type">;
interface ChoiceSelectProps extends TriggerProps {
  value: string | number;
  options: Choice[];
  onValueChange: (value: string) => void;
  required?: boolean;
  compact?: boolean;
  label?: string;
  prefix?: string;
}

export function ChoiceSelect({ value, options, onValueChange, required, compact = false, label, prefix, className = "", disabled, id: suppliedId, name, ...props }: ChoiceSelectProps) {
  const generatedId = useId();
  const id = suppliedId ?? generatedId;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [invalid, setInvalid] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number; width: number; maxHeight: number } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const search = useRef({ text: "", at: 0 });
  const selected = options.findIndex(option => option.value === String(value));
  const current = options[selected];
  const unavailable = disabled || !options.some(o => !o.disabled);
  const listId = `${id}-options`;

  function close() { setOpen(false); setPosition(null); }
  function show() {
    if (unavailable) return;
    setActive(selected >= 0 && !options[selected].disabled ? selected : options.findIndex(o => !o.disabled));
    search.current = { text: "", at: 0 };
    setOpen(true);
  }
  function choose(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    onValueChange(option.value);
    setInvalid(false);
    close();
    trigger.current?.focus({ preventScroll: true });
  }

  useLayoutEffect(() => {
    if (!open || !trigger.current || !menu.current) return;
    const rect = trigger.current.getBoundingClientRect();
    const height = Math.min(menu.current.scrollHeight, 320);
    const above = window.innerHeight - rect.bottom < height + 16 && rect.top > window.innerHeight - rect.bottom;
    const maxHeight = Math.max(44, Math.min(320, (above ? rect.top : window.innerHeight - rect.bottom) - 16));
    const width = Math.min(Math.max(rect.width, compact ? 244 : 220), window.innerWidth - 16);
    setPosition({ top: Math.max(8, above ? rect.top - Math.min(height, maxHeight) - 8 : rect.bottom + 8),
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)), width, maxHeight });
  }, [open, compact, options.length]);

  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>(`[data-active="true"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]);
  useEffect(() => { if (unavailable) close(); }, [unavailable]);
  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (!trigger.current?.contains(event.target as Node) && !menu.current?.contains(event.target as Node)) close();
    }
    function scroll(event: Event) { if (!menu.current?.contains(event.target as Node)) close(); }
    function dismiss() { close(); }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("scroll", scroll, true);
    window.addEventListener("resize", dismiss);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("scroll", scroll, true); window.removeEventListener("resize", dismiss); };
  }, [open]);

  function onKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Escape" && open) { event.preventDefault(); close(); return; }
    if (event.key === "Tab") { close(); return; }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (!open) {
        show();
        if (event.key === 'Home') setActive(options.findIndex(o => !o.disabled));
        if (event.key === 'End') setActive(options.findLastIndex(o => !o.disabled));
        return;
      }
      const enabled = options.flatMap((option, index) => option.disabled ? [] : [index]);
      if (!enabled.length) return;
      const index = enabled.indexOf(active);
      setActive(event.key === "Home" ? enabled[0] : event.key === "End" ? enabled.at(-1)! : enabled[(index + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length]);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) choose(active); else show();
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      if (!open) show();
      const at = Date.now();
      const text = (at - search.current.at > 700 ? "" : search.current.text) + event.key.toLocaleLowerCase();
      search.current = { text, at };
      const match = options.findIndex(option => !option.disabled && option.label.toLocaleLowerCase().startsWith(text));
      if (match >= 0) setActive(match);
    }
  }

  return <div className={compact ? "choice-control choice-control-compact" : "choice-control"}>
    <button {...props} ref={trigger} id={id} type="button" role="combobox" className={`choice-trigger ${compact ? "choice-trigger-compact" : ""} ${className}`}
      aria-label={props["aria-label"] ?? (label ? `${label}：${current?.label ?? "请选择"}` : undefined)} aria-haspopup="listbox" aria-expanded={open}
      aria-controls={open ? listId : undefined} aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
      aria-required={required || undefined} aria-invalid={invalid || undefined} disabled={unavailable} onKeyDown={onKey} onClick={() => open ? close() : show()}>
      {prefix && <span className="choice-prefix">{prefix}</span>}<span className={`choice-value ${current?.value === "" ? "text-muted" : ""}`}>{current?.label ?? "请选择"}</span>
      <Icon name="chevron" size={15} className={open ? "-rotate-90 shrink-0" : "rotate-90 shrink-0"} />
    </button>
    {(required || name) && <input className="choice-validation" aria-hidden="true" tabIndex={-1} name={name} value={current?.value ?? ""} required={required} disabled={unavailable} onChange={() => {}} onInvalid={event => { event.preventDefault(); setInvalid(true); trigger.current?.focus(); }} />}
    {invalid && <span role="alert" className="mt-2 block text-sm text-warn">请选择一项。</span>}
    {open && createPortal(<div ref={menu} id={listId} role="listbox" aria-label={label} aria-labelledby={label ? undefined : id} className="choice-menu" style={position ?? { visibility: "hidden" }}>
      {label && <p className="choice-menu-title">{label}</p>}
      {options.map((option, index) => <div id={`${listId}-${index}`} key={option.value} role="option" aria-selected={String(value) === option.value} aria-disabled={option.disabled || undefined}
        data-active={active === index} className="choice-option" onPointerMove={() => { if (!option.disabled) setActive(index); }} onPointerDown={event => event.preventDefault()} onClick={() => choose(index)}>
        <span>{option.label}</span>{String(value) === option.value && <Icon name="check" size={17} className="shrink-0" />}
      </div>)}
    </div>, trigger.current?.closest("dialog") ?? document.body)}
  </div>;
}

function text(children: ReactNode): string {
  return Children.toArray(children).map(child => typeof child === "string" || typeof child === "number" ? String(child) : isValidElement<{ children?: ReactNode }>(child) ? text(child.props.children) : "").join("");
}
function choices(children: ReactNode): Choice[] {
  return Children.toArray(children).flatMap(child => {
    if (!isValidElement<{ value?: string | number; disabled?: boolean; children?: ReactNode }>(child)) return [];
    if (child.type !== "option") return choices(child.props.children);
    const label = text(child.props.children);
    return [{ value: String(child.props.value ?? label), label, disabled: child.props.disabled }];
  });
}
export function Select({ children, ...props }: Omit<ChoiceSelectProps, "options" | "compact" | "label" | "prefix"> & { children: ReactNode }) {
  return <ChoiceSelect {...props} options={choices(children)} />;
}
