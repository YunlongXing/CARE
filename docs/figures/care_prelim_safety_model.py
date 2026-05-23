"""Draw the preliminary safety model figure for the CARE paper."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "figures"
PAPER_IMG_DIR = ROOT / "docs" / "paper" / "img"


def paper_font_family() -> list[str]:
    available = {font.name for font in font_manager.fontManager.ttflist}
    if "Times New Roman" in available:
        return ["Times New Roman"]
    if "Times" in available:
        return ["Times"]
    return ["Times New Roman", "Times", "DejaVu Serif"]


def box(ax, x, y, w, h, title, body, color, edge="#2F3A45"):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.025,rounding_size=0.05",
        facecolor=color,
        edgecolor=edge,
        linewidth=1.15,
        zorder=2,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h - 0.055, title, ha="center", va="top", fontsize=7.1, fontweight="bold")
    ax.text(x + w / 2, y + h - 0.22, body, ha="center", va="top", fontsize=7.1, linespacing=0.90)


def arrow(ax, start, end, color="#111827", dashed=False, rad=0.0, lw=1.4):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=8,
            linewidth=lw,
            color=color,
            linestyle=(0, (4, 3)) if dashed else "-",
            connectionstyle=f"arc3,rad={rad}",
            shrinkA=4,
            shrinkB=4,
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
    fig, ax = plt.subplots(figsize=(4.7, 1.28))
    ax.set_xlim(0.02, 5.44)
    ax.set_ylim(0.02, 1.30)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

    box(ax, 0.05, 0.82, 1.05, 0.42, "Original", "C/C++ + tests", "#E8F1FA")
    box(ax, 1.42, 0.82, 1.10, 0.42, "Candidate", "LLM diff", "#F3E8FF")
    box(ax, 2.86, 0.82, 1.10, 0.42, "Patched", "clean tree", "#FFF4DE")
    box(ax, 4.31, 0.82, 1.08, 0.42, "Accepted", "all gates pass", "#EAF7F7")

    arrow(ax, (1.10, 1.03), (1.42, 1.03), lw=1.15)
    arrow(ax, (2.52, 1.03), (2.86, 1.03), lw=1.15)
    arrow(ax, (3.96, 1.03), (4.31, 1.03), lw=1.15)

    box(
        ax,
        1.52,
        0.10,
        3.22,
        0.42,
        "Validation Gates",
        "apply -> compile/tests -> resource\nsemantic -> security",
        "#FDECEC",
        edge="#C83D4C",
    )
    arrow(ax, (3.41, 0.82), (3.41, 0.52), color="#C83D4C", dashed=True, lw=1.15)
    arrow(ax, (4.74, 0.32), (4.86, 0.82), color="#C83D4C", dashed=True, rad=-0.10, lw=1.15)

    return fig


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_IMG_DIR.mkdir(parents=True, exist_ok=True)
    fig = build()
    for ext in ("pdf", "png", "svg"):
        for directory in (OUT_DIR, PAPER_IMG_DIR):
            path = directory / f"care_prelim_safety_model.{ext}"
            if ext == "png":
                fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0, facecolor="white")
            else:
                fig.savefig(path, bbox_inches="tight", pad_inches=0, facecolor="white")
            print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
