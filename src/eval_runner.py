"""Run the historical golden cases through the real agent and grade what a machine can grade.

Machine checks (per case):
  tool_called         the expected tool was called with the golden arguments
  tool_result_matches that call returned the golden rows (after the order threshold)
  key_number_recall   share of the question's key numbers that appear in the answer
  ungrounded_numbers  numbers in the answer that no tool result supports
Everything else (reasoning, 口径, limitations) goes to a review sheet for people.

Usage:
  python -m eval_runner --package 回测包 --output-dir eval_runs/2026-10-19
  python -m eval_runner --package 回测包 --only A01,P02 --repeat 3 --output-dir ...
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
import sys

import historical_backtest as history
import research_assistant as research
from socialdog_agent.grounding import mentions, mentions_date
from socialdog_agent.history_tools import DEFAULT_MIN_ORDERS, suppress_price_windows, to_yuan
from socialdog_agent.llm import LLMError

TOOLS = {"describe": "history_describe", "aggregate": "history_aggregate", "price_events": "history_price_events",
         "quality": "history_quality", "unavailable": "history_unavailable"}
PRICE_DEFAULTS = {"product": None, "min_run_days": 3, "window_days": 14, "scope": "baseline"}
# Words in the question decide which row metrics count as key numbers.
KEYWORDS = [("订单", "order_count"), ("明细", "line_count"), ("件数", "recorded_quantity"),
            ("数量", "recorded_quantity"), ("收款", "recorded_receipt_yuan"), ("金额", "recorded_receipt_yuan"),
            ("成交", "recorded_deal_yuan")]
GROUPED_RECALL = 0.9
# The parts of a tool result compared with golden; envelopes carry tool-specific notes.
COMPARED = {"aggregate": ["rows"], "price_events": ["event_count", "events"]}
REVIEW_COLUMNS = ["推理过程(1-5)", "口径说明(1-5)", "局限性说明(1-5)", "评审意见"]


def normalized_arguments(operation: str, arguments: dict) -> dict:
    if operation == "price_events":
        return {**PRICE_DEFAULTS, **{k: v for k, v in arguments.items() if v is not None or k == "product"}}
    if operation == "aggregate":
        return {k: v for k, v in arguments.items() if not (k == "product" and v is None)}
    return dict(arguments)


def expected_result(case: dict, min_orders: int) -> dict:
    """The golden answer as the agent's tool should return it: yuan units, threshold applied."""
    expected = case["expected_result"]
    if case["operation"] == "describe":
        expected = {key: value for key, value in expected.items() if key != "source_rows_removed_after_as_of"}
    if case["operation"] == "aggregate" and expected.get("rows"):
        expected = {**expected, "rows": [r for r in expected["rows"] if r["order_count"] >= min_orders]}
    if case["operation"] == "price_events" and expected.get("events") is not None:
        expected = suppress_price_windows(expected, min_orders)
    return to_yuan(expected)


def subset_equal(expected, actual) -> bool:
    """Every key in expected is present in actual with the same value (extra keys allowed)."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset_equal(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and \
            all(subset_equal(e, a) for e, a in zip(expected, actual))
    if isinstance(expected, float) or isinstance(actual, float):
        return isinstance(actual, (int, float)) and isinstance(expected, (int, float)) and abs(expected - actual) < 1e-6
    return expected == actual


def matching_calls(case: dict, evidence: list[dict]) -> list[dict]:
    want = normalized_arguments(case["operation"], case["arguments"])
    calls = []
    for item in evidence:
        if item["tool"] != TOOLS[case["operation"]]:
            continue
        try:
            arguments = json.loads(item["arguments"])
        except (TypeError, ValueError):
            continue
        if normalized_arguments(case["operation"], arguments) == want:
            calls.append(item)
    return calls


def key_numbers(case: dict, expected: dict) -> list[tuple[str, object]]:
    """(label, value) pairs a correct answer should state; dates are checked as dates."""
    operation, question = case["operation"], case["question"]
    if operation == "describe":
        return [(k, expected[k]) for k in ["source_line_count", "source_order_count"]] + \
               [("observation_start", ("date", expected["observation_start"])),
                ("observation_end", ("date", expected["observation_end"]))]
    if operation == "aggregate" and expected.get("rows"):
        fields = list(dict.fromkeys(f for word, f in KEYWORDS if word in question)) or ["order_count"]
        return [(f"{row['group']}.{f}", row[f]) for row in expected["rows"] for f in fields if f in row]
    if operation == "price_events":
        result = []
        for event in expected.get("events", []):
            label = f"{event['product']}/{event['specification']}"
            result += [(label + ".event_date", ("date", event["event_date"])),
                       (label + ".old_price", event["old_unit_price_yuan"]),
                       (label + ".new_price", event["new_unit_price_yuan"])]
            if case["arguments"].get("product"):
                result += [(label + ".change_pct." + k, v) for k, v in event["change_pct"].items() if v is not None]
        return result
    if operation == "quality":
        return [(k, v) for k, v in expected.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) > 10]
    return []


def stated(answer: str, value) -> bool:
    if isinstance(value, tuple) and value[0] == "date":
        return mentions_date(answer, value[1])
    return mentions(answer, value)


def grade(case: dict, result: dict | None, error: str | None, min_orders: int) -> dict:
    record = {"case_id": case["case_id"], "operation": case["operation"], "question": case["question"],
              "error": error, "manual_checks": case.get("manual_checks", [])}
    if result is None:
        return {**record, "auto_pass": False, "auto_notes": ["agent未返回回答：" + (error or "")]}
    answer, evidence = result["answer"], result["evidence"]
    expected = expected_result(case, min_orders)
    calls = matching_calls(case, evidence)
    notes = []
    invalid = expected.get("status") == "error"
    if invalid:
        # An illegal query passes when no matching call succeeded; refusing before calling is fine too.
        succeeded = [c for c in calls if c["result"].get("status") == "ok"]
        record.update(tool_called=bool(calls), tool_result_matches=not succeeded)
        if succeeded:
            notes.append("非法参数的查询返回了结果")
    else:
        record["tool_called"] = bool(calls)
        compared = {k: expected[k] for k in COMPARED.get(case["operation"], expected) if k != "status"}
        record["tool_result_matches"] = any(subset_equal(compared, c["result"]) for c in calls)
        if not calls:
            notes.append(f"未用golden参数调用{TOOLS[case['operation']]}")
        elif not record["tool_result_matches"]:
            notes.append("工具结果与golden不一致")
    keys = [] if invalid else key_numbers(case, expected)
    missing = [label for label, value in keys if not stated(answer, value)]
    recall = 1.0 if not keys else round(1 - len(missing) / len(keys), 4)
    grouped = case["operation"] == "aggregate" and case["arguments"].get("by") != "all"
    recall_ok = recall >= (GROUPED_RECALL if grouped else 1.0)
    if not recall_ok:
        notes.append("回答缺少关键数字：" + "、".join(missing[:10]))
    ungrounded = result.get("grounding", {}).get("ungrounded_numbers", [])
    if ungrounded:
        notes.append("回答含工具结果中找不到的数字：" + "、".join(ungrounded[:10]))
    record.update(key_numbers_total=len(keys), key_number_recall=recall, missing_key_numbers=missing,
                  ungrounded_numbers=ungrounded, repair_attempted=result.get("grounding", {}).get("repair_attempted"),
                  tools_used=[e["tool"] for e in evidence], answer=answer, request_id=result.get("request_id"),
                  auto_notes=notes)
    record["auto_pass"] = bool((record["tool_called"] or invalid) and record["tool_result_matches"]
                               and recall_ok and not ungrounded)
    return record


def run_cases(agent, cases: list[dict], min_orders: int, repeat: int = 1, on_result=None) -> list[dict]:
    records = []
    for run in range(repeat):
        for case in cases:
            result, error = None, None
            try:
                result = agent.ask(case["question"])
            except (LLMError, ValueError) as caught:
                error = str(caught)
            record = {"run": run + 1, **grade(case, result, error, min_orders)}
            records.append(record)
            if on_result:
                on_result(record)
    return records


def summarize(records: list[dict]) -> dict:
    by_kind = Counter()
    passed = Counter()
    for r in records:
        kind = r["case_id"][0]
        by_kind[kind] += 1
        passed[kind] += r["auto_pass"]
    return {"cases_run": len(records), "auto_passed": sum(r["auto_pass"] for r in records),
            "by_category": {k: {"run": by_kind[k], "auto_passed": passed[k]} for k in sorted(by_kind)},
            "agent_errors": sum(r["error"] is not None for r in records),
            "answers_with_ungrounded_numbers": sum(bool(r.get("ungrounded_numbers")) for r in records),
            "tool_called": sum(bool(r.get("tool_called")) for r in records),
            "tool_result_matches": sum(bool(r.get("tool_result_matches")) for r in records)}


def write_outputs(folder: Path, records: list[dict], meta: dict) -> dict:
    folder.mkdir(parents=True, exist_ok=False)
    with (folder / "results.jsonl").open("w", encoding="utf-8") as stream:
        for r in records:
            stream.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {**meta, **summarize(records)}
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # People only judge what the machine cannot: the numbers are already checked above.
    with (folder / "review_sheet.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["run", "case_id", "question", "answer", "机器判定", "机器备注", "人工核查要点"] + REVIEW_COLUMNS)
        for r in records:
            writer.writerow([r["run"], r["case_id"], r["question"], r.get("answer") or r.get("error") or "",
                             "通过" if r["auto_pass"] else "未通过", "；".join(r["auto_notes"]),
                             "；".join(r["manual_checks"])] + [""] * len(REVIEW_COLUMNS))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实DeepSeek agent跑历史golden用例并自动比对数字")
    parser.add_argument("--package", type=Path, required=True, help="historical_backtest.py build 生成的回测包")
    parser.add_argument("--cases", type=Path, help="默认使用回测包内的golden_cases.jsonl")
    parser.add_argument("--only", help="逗号分隔的case_id，例如A01,P02")
    parser.add_argument("--repeat", type=int, default=1, help="每个用例重复次数，用于观察稳定性")
    parser.add_argument("--min-orders", type=int, default=DEFAULT_MIN_ORDERS)
    parser.add_argument("--state-dir", type=Path, default=research.DEFAULT_STATE)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    import chat_agent
    from socialdog_agent import ResearchAgent, build_history_tools

    try:
        history.read_package(args.package)
        path = args.cases or args.package / "golden_cases.jsonl"
        cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if args.only:
            wanted = set(args.only.split(","))
            cases = [c for c in cases if c["case_id"] in wanted]
        if not cases or args.repeat < 1:
            raise ValueError("没有可运行的用例，或repeat小于1。")
        settings = chat_agent.load_settings()
        agent = ResearchAgent(chat_agent.make_llm(settings), state=args.state_dir,
                              tool_factory=lambda: build_history_tools(args.package, args.state_dir, args.min_orders))
    except (ValueError, OSError, KeyError, LLMError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 1

    def progress(record):
        mark = "✓" if record["auto_pass"] else "✗"
        print(f"[{record['run']}] {record['case_id']} {mark} " + "；".join(record["auto_notes"]), flush=True)

    records = run_cases(agent, cases, args.min_orders, args.repeat, progress)
    summary = write_outputs(args.output_dir, records, {
        "started_for_package": history.read_package(args.package)["data_description"]["dataset_version"],
        "model": settings["DEEPSEEK_MODEL"], "reasoning_effort": settings["DEEPSEEK_REASONING_EFFORT"],
        "min_orders": args.min_orders, "repeat": args.repeat,
        "finished_at": datetime.now(research.TZ).isoformat()})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["auto_passed"] == summary["cases_run"] else 2


if __name__ == "__main__":
    sys.exit(main())
