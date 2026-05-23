"""Draw a publication-ready CARE system architecture figure.

Run from the repository root:

    python3 docs/figures/care_system_architecture.py

Outputs:
    docs/figures/care_system_architecture.pdf
    docs/figures/care_system_architecture.png
    docs/figures/care_system_architecture.svg
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import patheffects as pe
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "figures"


COLORS = {
    "input": "#E8F1FA",
    "analysis": "#E9F7EF",
    "detect": "#FFF2CC",
    "llm": "#F3E8FF",
    "validate": "#FDECEC",
    "refine": "#EDE7F6",
    "output": "#EAF7F7",
    "experiment": "#F4F6F8",
    "border": "#2F3A45",
    "arrow": "#3F4A54",
    "muted": "#6B7280",
}


def add_box(
    ax,
    xy: tuple[float, float],
    width: float,
    height: float,
    title: str,
    lines: list[str],
    facecolor: str,
    fontsize: float = 8.4,
    title_fontsize: float = 9.2,
) -> FancyBboxPatch:
    x, y = xy
    patch = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.018,rounding_size=0.035",
        linewidth=1.15,
        edgecolor=COLORS["border"],
        facecolor=facecolor,
        zorder=2,
    )
    patch.set_path_effects(
        [
            pe.SimplePatchShadow(offset=(1.0, -1.0), alpha=0.11, rho=0.96),
            pe.Normal(),
        ]
    )
    ax.add_patch(patch)
    ax.text(
        x + width / 2,
        y + height - 0.16,
        title,
        ha="center",
        va="top",
        fontsize=title_fontsize,
        fontweight="bold",
        color="#111827",
        zorder=3,
    )
    ax.text(
        x + width / 2,
        y + height - 0.39,
        "\n".join(lines),
        ha="center",
        va="top",
        fontsize=fontsize,
        linespacing=1.22,
        color="#1F2937",
        zorder=3,
    )
    return patch


def add_label(ax, xy: tuple[float, float], text: str, color: str = "#111827") -> None:
    ax.text(
        xy[0],
        xy[1],
        text,
        ha="center",
        va="center",
        fontsize=8.2,
        color=color,
        zorder=4,
    )


def arrow(
    ax,
    start: tuple[float, float],
    end: tuple[float, float],
    connectionstyle: str = "arc3,rad=0.0",
    color: str = COLORS["arrow"],
    lw: float = 1.35,
    mutation_scale: float = 12,
    linestyle: str = "-",
) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=mutation_scale,
            linewidth=lw,
            color=color,
            linestyle=linestyle,
            connectionstyle=connectionstyle,
            shrinkA=4,
            shrinkB=4,
            zorder=1,
        )
    )


def dashed_panel(
    ax,
    xy: tuple[float, float],
    width: float,
    height: float,
    title: str,
) -> None:
    x, y = xy
    panel = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.02,rounding_size=0.04",
        linewidth=1.05,
        edgecolor="#9AA4B2",
        facecolor=COLORS["experiment"],
        linestyle=(0, (4, 3)),
        zorder=0,
    )
    ax.add_patch(panel)
    ax.text(
        x + 0.18,
        y + height - 0.12,
        title,
        ha="left",
        va="top",
        fontsize=9.0,
        fontweight="bold",
        color="#374151",
        zorder=1,
    )


def build_figure() -> plt.Figure:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.unicode_minus": False,
        }
    )

    fig, ax = plt.subplots(figsize=(16.9, 8.7))
    ax.set_xlim(0, 16.9)
    ax.set_ylim(0, 8.5)
    ax.axis("off")

    ax.text(
        8.45,
        8.13,
        "CARE: Context-aware Automated Refactoring Engine",
        ha="center",
        va="center",
        fontsize=17.0,
        fontweight="bold",
        color="#111827",
    )
    ax.text(
        8.45,
        7.78,
        "Security-aware program context, LLM patch generation, validation, and feedback-guided repair",
        ha="center",
        va="center",
        fontsize=10.2,
        color=COLORS["muted"],
    )

    y = 5.55
    h = 1.45
    w = 2.22
    gap = 0.24
    boxes = {}
    x0 = 0.35
    stages = [
        (
            "input",
            "Target Codebase",
            ["C/C++ source tree", "build/test commands", "compile database"],
            COLORS["input"],
        ),
        (
            "analysis",
            "Program Understanding",
            ["AST + functions", "CFG/call graph/data-flow", "resource + side effects"],
            COLORS["analysis"],
        ),
        (
            "detect",
            "Opportunity Detection",
            ["goto/resource/dead code", "duplicate checks/smells", "gating + clang confirm"],
            COLORS["detect"],
        ),
        (
            "llm",
            "LLM Refactoring",
            ["planner builds invariants", "prompt builder + client", "patch generator"],
            COLORS["llm"],
        ),
        (
            "validate",
            "Validation Pipeline",
            ["patch apply + compile", "tests + static analysis", "resource/semantic", "security checks"],
            COLORS["validate"],
        ),
        (
            "output",
            "Outputs",
            ["selected patches", "JSON/Markdown reports", "diffs + taxonomy"],
            COLORS["output"],
        ),
    ]

    for index, (key, title, lines, color) in enumerate(stages):
        x = x0 + index * (w + gap)
        boxes[key] = add_box(ax, (x, y), w, h, title, lines, color, fontsize=7.9, title_fontsize=8.8)
        if index > 0:
            prev_x = x0 + (index - 1) * (w + gap)
            arrow(ax, (prev_x + w, y + h / 2), (x, y + h / 2))

    # Context artifacts shared by detectors, planner, and validator.
    ctx = add_box(
        ax,
        (2.86, 3.65),
        3.15,
        1.35,
        "Context Graph",
        [
            "function/basic-block nodes",
            "call/control/data/resource edges",
            "error paths + return summaries",
        ],
        "#E6F4EA",
        fontsize=8.0,
    )
    kb = add_box(
        ax,
        (6.55, 3.65),
        2.55,
        1.35,
        "Security Knowledge",
        [
            "resource lifecycle rules",
            "unsafe API/error patterns",
            "validation semantics",
        ],
        "#FFF7E6",
        fontsize=8.0,
    )
    fail = add_box(
        ax,
        (9.85, 3.65),
        2.75,
        1.35,
        "Feedback + Repair",
        [
            "failure taxonomy",
            "validator logs/counterexamples",
            "smaller safer regenerated diffs",
        ],
        COLORS["refine"],
        fontsize=8.0,
    )

    arrow(ax, (3.83, y), (3.83, 5.02), connectionstyle="arc3,rad=0.0")
    arrow(ax, (4.58, 5.00), (7.15, y), connectionstyle="arc3,rad=-0.12")
    arrow(ax, (6.01, 4.30), (6.55, 4.30))
    arrow(ax, (9.10, 4.30), (9.85, 4.30))
    arrow(ax, (11.22, 5.00), (11.22, y), connectionstyle="arc3,rad=0.0")
    arrow(
        ax,
        (11.05, 3.65),
        (8.70, 5.55),
        connectionstyle="arc3,rad=-0.35",
        color="#6D28D9",
        lw=1.55,
    )
    add_label(ax, (10.10, 4.98), "failed candidate")
    add_label(ax, (9.05, 5.14), "repair loop", color="#5B21B6")

    # Validation detail strip.
    dashed_panel(ax, (0.52, 2.08), 15.90, 1.15, "Validation stages and safety gates")
    validation_steps = [
        ("restore clean tree", 1.45),
        ("apply path-repaired diff", 3.35),
        ("compile", 5.10),
        ("tests", 6.52),
        ("static analyzers", 7.92),
        ("resource checker", 9.72),
        ("semantic checker", 12.05),
        ("security checker", 14.10),
    ]
    for label, x in validation_steps:
        ax.text(
            x,
            2.57,
            label,
            ha="center",
            va="center",
            fontsize=8.0,
            color="#1F2937",
            bbox=dict(
                boxstyle="round,pad=0.18,rounding_size=0.03",
                facecolor="white",
                edgecolor="#CBD5E1",
                linewidth=0.8,
            ),
            zorder=3,
        )
    for (_, x1), (_, x2) in zip(validation_steps, validation_steps[1:]):
        arrow(ax, (x1 + 0.62, 2.57), (x2 - 0.62, 2.57), mutation_scale=9, lw=1.0)

    # Experimental/scale-out layer.
    dashed_panel(ax, (0.52, 0.34), 15.90, 1.48, "OSS50 evaluation and scale-out runner")
    exp_boxes = [
        (
            "Full scan",
            ["50 projects", "25k+ source files", "ranked opportunities"],
            1.95,
            "#FFFFFF",
        ),
        (
            "High-impact queue",
            ["critical/high", "evidence confidence", "resume metadata"],
            5.15,
            "#FFFFFF",
        ),
        (
            "Dynamic workers",
            ["work-id reservations", "isolated project copies", "process-level parallelism"],
            8.55,
            "#FFFFFF",
        ),
        (
            "Paper tables",
            ["CARE vs LLM-only", "validator ablation", "review labels"],
            12.15,
            "#FFFFFF",
        ),
    ]
    last_x = None
    for title, lines, center_x, color in exp_boxes:
        add_box(
            ax,
            (center_x - 1.15, 0.58),
            2.3,
            0.78,
            title,
            lines,
            color,
            fontsize=6.75,
            title_fontsize=8.0,
        )
        if last_x is not None:
            arrow(ax, (last_x + 1.12, 1.08), (center_x - 1.12, 1.08), mutation_scale=9, lw=1.0)
        last_x = center_x

    # Data dependencies into the evaluation layer.
    arrow(ax, (14.87, y + 0.38), (13.38, 1.36), connectionstyle="arc3,rad=0.38", linestyle="--", lw=1.05)
    arrow(ax, (8.34, 2.08), (8.54, 1.82), connectionstyle="arc3,rad=0.0", linestyle="--", lw=1.05)

    ax.text(
        0.55,
        0.22,
        "Figure: CARE integrates static program context with LLM patch generation and multi-stage validation; failed candidates feed back into repair.",
        ha="left",
        va="center",
        fontsize=8.0,
        color=COLORS["muted"],
    )

    return fig


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig = build_figure()
    for ext in ("pdf", "png", "svg"):
        path = OUT_DIR / f"care_system_architecture.{ext}"
        if ext == "png":
            fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
        else:
            fig.savefig(path, bbox_inches="tight", facecolor="white")
        print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
