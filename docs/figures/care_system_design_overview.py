"""Draw a compact single-column system design figure for the CARE paper."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "figures"
PAPER_IMG_DIR = ROOT / "docs" / "paper" / "img"


COLORS = {
    "input": "#E8F1FA",
    "context": "#E9F7EF",
    "detect": "#FFF4DE",
    "contract": "#FBF7EF",
    "llm": "#F0E9FA",
    "validate": "#FDECEC",
    "report": "#EAF7F7",
    "border": "#27313D",
    "arrow": "#334155",
    "feedback": "#B42335",
}


def paper_font_family() -> list[str]:
    available = {font.name for font in font_manager.fontManager.ttflist}
    if "Times New Roman" in available:
        return ["Times New Roman"]
    if "Times" in available:
        return ["Times"]
    return ["Times New Roman", "Times", "DejaVu Serif"]


def box(ax, x, y, w, h, title, body, color):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.006,rounding_size=0.030",
        linewidth=0.9,
        edgecolor=COLORS["border"],
        facecolor=color,
        zorder=2,
    )
    ax.add_patch(patch)
    ax.text(
        x + 0.08,
        y + h - 0.06,
        title,
        ha="left",
        va="top",
        fontsize=7.4,
        fontweight="bold",
        color="#111827",
    )
    ax.text(
        x + 0.08,
        y + h - 0.18,
        body,
        ha="left",
        va="top",
        fontsize=6.45,
        linespacing=1.0,
        color="#1F2937",
    )


def arrow(ax, start, end, *, dashed=False, color=None, rad=0.0):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=8.5,
            linewidth=1.0,
            linestyle=(0, (3, 2.5)) if dashed else "-",
            color=color or COLORS["arrow"],
            connectionstyle=f"arc3,rad={rad}",
            shrinkA=3,
            shrinkB=3,
            zorder=1,
        )
    )


def build():
    plt.rcParams.update(
        {
            "font.family": paper_font_family(),
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, ax = plt.subplots(figsize=(3.35, 3.65))
    ax.set_xlim(0, 3.35)
    ax.set_ylim(0, 3.65)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

    x = 0.08
    w = 2.88
    stages = [
        (3.27, 0.31, "Target Project", "source tree | build/test commands | compile DB", COLORS["input"]),
        (
            2.74,
            0.41,
            "Security-Aware Context Graph",
            "functions/blocks + call/control/data/resource edges\nerror paths, return summaries, side effects",
            COLORS["context"],
        ),
        (
            2.22,
            0.37,
            "Opportunity Detection",
            "goto/resource/duplicate/dead/smell detectors\nseverity, confidence, evidence, ranking",
            COLORS["detect"],
        ),
        (
            1.73,
            0.37,
            "Safety Contract",
            "affected files/functions, invariants,\nsecurity constraints, validation strategy",
            COLORS["contract"],
        ),
        (
            1.24,
            0.37,
            "LLM Patch Candidates",
            "JSON edit plan or unified diff\nconservative/aggressive generation modes",
            COLORS["llm"],
        ),
        (
            0.62,
            0.43,
            "Validation Gates",
            "apply -> build/tests -> static analysis\nresource -> semantic -> security checks",
            COLORS["validate"],
        ),
        (
            0.08,
            0.31,
            "Report",
            "selected patch | failed candidates | traces",
            COLORS["report"],
        ),
    ]

    for y, h, title, body, color in stages:
        box(ax, x, y, w, h, title, body, color)

    for (y1, h1, *_), (y2, h2, *__) in zip(stages[:-1], stages[1:]):
        arrow(ax, (x + w / 2, y1), (x + w / 2, y2 + h2))

    # Feedback loop from validation failures back to patch generation.
    arrow(
        ax,
        (x + w + 0.02, 0.84),
        (x + w + 0.02, 1.42),
        dashed=True,
        color=COLORS["feedback"],
        rad=0.0,
    )
    arrow(
        ax,
        (x + w + 0.02, 1.42),
        (x + w, 1.43),
        dashed=True,
        color=COLORS["feedback"],
    )
    ax.text(
        x + w + 0.07,
        1.14,
        "feedback",
        ha="left",
        va="center",
        fontsize=6.2,
        rotation=90,
        color=COLORS["feedback"],
    )

    return fig


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_IMG_DIR.mkdir(parents=True, exist_ok=True)
    fig = build()
    for ext in ("pdf", "png", "svg"):
        for directory in (OUT_DIR, PAPER_IMG_DIR):
            path = directory / f"care_system_design_overview.{ext}"
            if ext == "png":
                fig.savefig(path, dpi=600, facecolor="white")
            else:
                fig.savefig(path, facecolor="white")
            print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
