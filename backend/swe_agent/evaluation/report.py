"""Experiment report generator.

Formats the SQL-derived tables of `metrics.experiment_tables` into report.md + CSV files (and a
PNG chart when matplotlib is installed). It never computes a metric itself except the stated
cost *estimate*, whose assumptions are printed next to the number.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERIES = "#2a78d6"  # one hue: a single series needs no legend
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

HYPOTHESES = [
    ("H1", "The agent (C) has a higher task success rate than ReAct (B) and single-shot (A)."),
    ("H2", "The agent has a higher recovery rate than ReAct (B)."),
    ("H3", "The reproduce step improves success on medium/hard tasks (full vs no_reproduce)."),
    ("H4", "Classified failure routing beats generic retry (full vs generic_retry)."),
]


@dataclass
class CostAssumptions:
    usd_per_mtok_in: float = 0.0
    usd_per_mtok_out: float = 0.0
    gpu_usd_per_hour: float = 0.0


def _label(row: dict[str, Any]) -> str:
    return row["system"] if row["variant"] == "full" else f"{row['system']}:{row['variant']}"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def _num(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x:,.{digits}f}"


def _sec(ms: float | None) -> str:
    return "-" if ms is None else f"{ms / 1000:,.0f}s"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _spread(tables: dict[str, Any], label: str) -> str:
    rates = [r["success_rate"] for r in tables["repeat_success"] if _label(r) == label
             and r["success_rate"] is not None]  # fmt: skip
    if len(rates) < 2:
        return "-"
    return f"{100 * min(rates):.0f}–{100 * max(rates):.0f}%"


def render_markdown(tables: dict[str, Any], cost: CostAssumptions) -> str:
    exp = tables["experiment"]
    cfg = exp.get("config") or {}
    model = cfg.get("model") or {}
    summary = tables["summary"]
    n_tasks = len(cfg.get("task_ids") or [])
    lines = [f"# Experiment report: {exp['name']} ({exp['id']})", ""]
    if cfg.get("scripted"):
        lines += [
            "> **WARNING: these runs used a scripted (fake) model. The numbers below test the "
            "harness only and are NOT model performance.**",
            "",
        ]
    lines += [
        f"- status: {exp['status']}; created {exp['created_at']}; finished {exp.get('finished_at') or '-'}",  # noqa: E501
        f"- model: provider `{model.get('provider')}`, reasoning `{model.get('reasoning')}`, "
        f"coder `{model.get('coder')}`, tool mode `{model.get('tool_mode')}`, "
        f"temperature {model.get('temperature')}",
        f"- tasks: {n_tasks}; repeats: {cfg.get('repeats')}; seeds: {cfg.get('seeds') or model.get('seed')}",  # noqa: E501
        f"- prompts: agent `{cfg.get('prompt_version')}`, baselines "
        f"`{cfg.get('baseline_prompt_version')}`; agent version `{cfg.get('agent_version')}`",
        f"- budget per run: {cfg.get('budget')}",
        "",
        "Success = every held-out test passes and no visible test regresses, on a fresh copy of "
        "the task (the agent never sees the held-out tests). All numbers come from the SQL views "
        "in migration 0003.",
        "",
    ]
    if n_tasks:
        pp = 100 / n_tasks
        lines += [
            f"**Noise.** With {n_tasks} tasks one task is {pp:.0f} percentage points. "
            f"Differences smaller than about two tasks ({2 * pp:.0f} pp) should be treated as "
            "noise, especially with fewer than 3 repeats.",
            "",
        ]
    lines += ["## Summary", ""]
    lines.append(_table(
        ["system", "runs", "success", "spread (repeats)", "first attempt", "held-out pass",
         "recovery", "iterations", "wall p50 / p90", "tokens in / out (mean)", "tool calls",
         "repeated calls", "plan deviation", "tamper attempts"],
        [[_label(r), f"{r['runs']} ({r['evaluated']} scored)",
          f"{r['successes']}/{r['evaluated']} ({_pct(r['success_rate'])})",
          _spread(tables, _label(r)), _pct(r["first_attempt_success_rate"]),
          _pct(r["final_test_pass_rate"]), _pct(r["recovery_rate"]),
          _num(r["mean_iterations"]), f"{_sec(r['wall_ms_p50'])} / {_sec(r['wall_ms_p90'])}",
          f"{_num(r['mean_tokens_in'], 0)} / {_num(r['mean_tokens_out'], 0)}"
          + (f" ({r['runs_without_token_data']} runs without usage)"
             if r["runs_without_token_data"] else ""),
          _num(r["mean_tool_calls"]), _num(r["mean_repeated_calls"]),
          _pct(r["plan_deviation_rate"]), str(r["tamper_attempts"] or 0)]
         for r in summary],
    ))  # fmt: skip
    lines += [
        "",
        "Recovery = among runs whose first patch did not succeed, the fraction that succeeded "
        "later. Repeated calls = tool calls identical to an earlier call with no edit in "
        "between. Plan deviation applies to the agent only (baselines have no plan).",
        "",
        "## Latency breakdown (mean per run)",
        "",
        _table(
            ["system", "model", "tools", "sandbox"],
            [
                [
                    _label(r),
                    _sec(r["mean_model_ms"]),
                    _sec(r["mean_tool_ms"]),
                    _sec(r["mean_sandbox_ms"]),
                ]
                for r in summary
            ],
        ),
        "",
        "## Estimated cost",
        "",
        f"Assumptions (stated, not measured): ${cost.usd_per_mtok_in}/M input tokens, "
        f"${cost.usd_per_mtok_out}/M output tokens, GPU ${cost.gpu_usd_per_hour}/hour of active "
        "run time. Local models have no token price; set these to compare with a hosted API.",
        "",
        _table(
            ["system", "token cost (total)", "GPU cost (total)"],
            [[_label(r), _usd_tokens(r, cost), _usd_gpu(r, cost)] for r in summary],
        ),
        "",
    ]
    lines += _breakdown(tables, "by_difficulty", "difficulty", "By difficulty")
    lines += _breakdown(tables, "by_category", "category", "By category")
    lines += ["## Termination reasons", ""]
    lines.append(_table(["system", "reason", "runs"],
                        [[_label(r), str(r["reason"]), str(r["runs"])]
                         for r in tables["terminations"]]))  # fmt: skip
    lines += ["", "## Failure categories seen during runs (agent)", ""]
    fc = tables["failure_categories"]
    lines.append(_table(["system", "category", "occurrences"],
                        [[_label(r), r["category"], str(r["occurrences"])] for r in fc])
                 if fc else "none recorded")  # fmt: skip
    lines += ["", "## Per task (successes / runs)", ""]
    labels = sorted({_label(r) for r in tables["per_task"]})
    per: dict[str, dict[str, str]] = {}
    meta: dict[str, str] = {}
    for r in tables["per_task"]:
        per.setdefault(r["benchmark_task"], {})[_label(r)] = f"{r['successes']}/{r['runs']}"
        meta[r["benchmark_task"]] = f"{r['difficulty']}, {r['category']}"
    lines.append(_table(["task", "difficulty, category", *labels],
                        [[t, meta[t], *[per[t].get(lab, "-") for lab in labels]]
                         for t in sorted(per)]))  # fmt: skip
    lines += ["", "## Pre-registered hypotheses (README: Evaluation)", "",
              "Stated before any run. The numbers relevant to each are in the tables above; "
              "whether they hold is a judgement for the reader, including when they do not.",
              ""]  # fmt: skip
    lines += [f"- **{h}**: {text}" for h, text in HYPOTHESES]
    return "\n".join(lines) + "\n"


def _usd_tokens(r: dict[str, Any], c: CostAssumptions) -> str:
    if r["runs_without_token_data"] or r["total_tokens_in"] is None:
        return "- (token usage missing for some runs)"
    usd = r["total_tokens_in"] * c.usd_per_mtok_in + r["total_tokens_out"] * c.usd_per_mtok_out
    return f"${usd / 1e6:,.2f}"


def _usd_gpu(r: dict[str, Any], c: CostAssumptions) -> str:
    if r["total_active_ms"] is None:
        return "-"
    return f"${r['total_active_ms'] / 3.6e6 * c.gpu_usd_per_hour:,.2f}"


def _breakdown(tables: dict[str, Any], key: str, col: str, title: str) -> list[str]:
    rows = tables[key]
    labels = sorted({_label(r) for r in rows})
    groups = sorted({r[col] for r in rows})
    cell = {(r[col], _label(r)): f"{r['successes']}/{r['runs']}" for r in rows}
    return [f"## {title} (successes / runs)", "",
            _table([col, *labels], [[g, *[cell.get((g, lab), "-") for lab in labels]]
                                    for g in groups]), ""]  # fmt: skip


def write_csvs(tables: dict[str, Any], out: Path) -> list[Path]:
    paths = []
    for key in ("summary", "per_task", "runs"):
        rows = tables[key]
        path = out / f"{key}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        paths.append(path)
    return paths


def write_chart(tables: dict[str, Any], out: Path) -> Path | None:
    """Horizontal bars: held-out success rate per system, min-max across repeats as whiskers."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    rows = [r for r in tables["summary"] if r["evaluated"]]
    if not rows:
        return None
    labels = [_label(r) for r in rows][::-1]
    rates = [100 * (r["success_rate"] or 0) for r in rows][::-1]
    fig, ax = plt.subplots(figsize=(7.5, 0.55 * len(rows) + 1.4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.barh(labels, rates, color=SERIES, height=0.55)
    for i, lab in enumerate(labels):
        reps = [100 * x["success_rate"] for x in tables["repeat_success"]
                if _label(x) == lab and x["success_rate"] is not None]  # fmt: skip
        right = rates[i]
        if len(reps) >= 2:
            ax.plot([min(reps), max(reps)], [i, i], color=INK_2, linewidth=1.5)
            right = max(right, max(reps))
        r = rows[len(rows) - 1 - i]
        ax.text(right + 2, i, f"{rates[i]:.0f}%  ({r['successes']}/{r['evaluated']})",
                va="center", fontsize=8, color=INK)  # fmt: skip
    ax.set_xlim(0, 118)
    ax.set_xlabel("held-out success rate (%); line = min–max across repeats", color=INK_2,
                  fontsize=8)  # fmt: skip
    ax.tick_params(colors=INK_2, labelsize=8, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    title = "Held-out success rate by system"
    if (tables["experiment"].get("config") or {}).get("scripted"):
        title += " (SCRIPTED MODEL: harness test, not performance)"
    ax.set_title(title, loc="left", fontsize=10, color=INK)
    fig.tight_layout()
    path = out / "success_rate.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def generate_report(tables: dict[str, Any], out: Path, cost: CostAssumptions) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    md = render_markdown(tables, cost)
    chart = write_chart(tables, out)
    if chart is not None:
        md = md.replace("## Summary\n", f"## Summary\n\n![success rate]({chart.name})\n", 1)
    write_csvs(tables, out)
    path = out / "report.md"
    path.write_text(md, encoding="utf-8")
    return path
