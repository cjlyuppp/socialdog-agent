import { cloneElement, isValidElement, useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { Icon } from "./Icon";
export { Select } from './Select';

type Variant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<Variant, string> = {
  primary: "bg-ink text-white hover:bg-[#414a3c] disabled:bg-[#e1e4d9] disabled:text-muted",
  secondary: "border border-line bg-surface hover:bg-sidebar disabled:text-faint",
  ghost: "text-muted hover:bg-[#eceee6] hover:text-ink disabled:text-faint",
  danger: "border border-[#ecdacb] bg-warn-soft text-warn hover:bg-[#f6e6d8]",
};

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 h-10 text-sm font-medium transition-colors disabled:cursor-not-allowed ${variants[variant]} ${className}`}
      {...props}
    />
  );
}

const control = "min-w-0 w-full rounded-lg border border-[#cdd4c5] bg-surface px-3 text-sm placeholder:text-muted focus:border-[#748367]";

export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${control} h-10 ${className}`} {...props} />;
}

export function Textarea({ className = "", ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${control} py-2.5 leading-6 resize-y ${className}`} {...props} />;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  const generated = useId();
  const child = isValidElement<{ id?: string; 'aria-describedby'?: string }>(children) ? children : null;
  const id = child?.props.id ?? generated;
  return (
    <div className="block">
      <label htmlFor={id} className="mb-2 block text-sm font-medium text-ink">{label}</label>
      {child ? cloneElement(child, { id, 'aria-describedby': [child.props['aria-describedby'], hint ? `${id}-hint` : null].filter(Boolean).join(' ') || undefined }) : children}
      {hint && <span id={`${id}-hint`} className="mt-2 block text-xs leading-5 text-muted">{hint}</span>}
    </div>
  );
}

export function Card({ className = "", children }: { className?: string; children: ReactNode }) {
  return <section className={`rounded-xl border border-line bg-surface ${className}`}>{children}</section>;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "warn" | "accent"; children: ReactNode }) {
  const tones = {
    neutral: "bg-sidebar text-muted",
    ok: "bg-ok-soft text-ok",
    warn: "bg-warn-soft text-warn",
    accent: "bg-accent-soft text-accent",
  };
  return <span className={`inline-flex items-center rounded-md px-2 py-1 text-xs whitespace-nowrap ${tones[tone]}`}>{children}</span>;
}

export function Notice({ tone = "warn", children }: { tone?: "warn" | "ok" | "neutral"; children: ReactNode }) {
  const tones = {
    warn: "border-[#ecdacb] bg-warn-soft text-warn",
    ok: "border-[#d3e2d8] bg-ok-soft text-ok",
    neutral: "border-line bg-sidebar text-muted",
  };
  return <div role="status" className={`rounded-lg border px-4 py-3 text-sm leading-6 ${tones[tone]}`}>{children}</div>;
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[28px] leading-snug font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-3 max-w-2xl text-sm leading-7 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col items-center py-12 text-center text-muted">
      <Icon name="data" size={24} />
      <div className="mt-4 text-sm leading-7">{children}</div>
    </div>
  );
}

export function Spinner({ label = "加载中" }: { label?: string }) {
  return <div role="status" className="py-8 text-center text-sm text-muted animate-pulse">{label}…</div>;
}
