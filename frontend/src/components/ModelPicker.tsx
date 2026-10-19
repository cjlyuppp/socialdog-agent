import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ModelSelection, ReasoningEffort } from "../api/types";
import { useMe } from "../lib/auth";
import { errorText } from "../lib/format";
import { Notice } from "./ui";
import { ChoiceSelect } from "./Select";

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


export function ModelPicker({ controls, disabled = false }: { controls: ReturnType<typeof useModelSelection>; disabled?: boolean }) {
  const { catalog, selection, change } = controls;
  if (catalog.error) return <Notice>{errorText(catalog.error)} <button type="button" className="underline" onClick={() => catalog.refetch()}>重试</button></Notice>;
  if (!selection) return <span role="status" className="text-sm text-muted">正在加载模型…</span>;
  const model = catalog.data!.models.find(m => m.id === selection.model)!;
  return <div className="model-controls">
    <ChoiceSelect compact label="模型" disabled={disabled} value={selection.model} options={catalog.data!.models.map(m => ({ value: m.id, label: m.label }))} onValueChange={model => change({ model })} />
    <ChoiceSelect compact label="思考强度" prefix="思考" disabled={disabled} value={selection.reasoning_effort} options={model.reasoning_efforts.map(value => ({ value, label: EFFORT_LABELS[value] }))} onValueChange={reasoning_effort => change({ reasoning_effort: reasoning_effort as ReasoningEffort })} />
  </div>;
}
