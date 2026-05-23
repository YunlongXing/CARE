"""Generate evaluation figures for the CARE paper."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import Patch


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


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.family": paper_font_family(),
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 1.4,
            "xtick.major.width": 1.4,
            "ytick.major.width": 1.4,
            "xtick.major.size": 4.5,
            "ytick.major.size": 4.5,
        }
    )


def style_axes(ax) -> None:
    for spine in ax.spines.values():
        spine.set_linewidth(1.4)
        spine.set_color("black")
    ax.tick_params(axis="both", labelsize=8.3, width=1.4, color="black")
    ax.grid(axis="y", color="#D1D5DB", linewidth=0.55, alpha=0.85)
    ax.set_axisbelow(True)


def annotate_bar(ax, bar, text: str, *, dy: float = 0.9, fontsize: float = 7.4) -> None:
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height() + dy,
        text,
        ha="center",
        va="bottom",
        fontsize=fontsize,
        color="black",
    )


def draw_yield_figure():
    categories = ["Apply", "Validate"]
    care = np.array([39.63, 6.37])
    llm_only = np.array([12.94, 1.03])
    x = np.arange(len(categories))
    width = 0.28

    fig, ax = plt.subplots(figsize=(3.35, 2.15))
    fig.subplots_adjust(left=0.16, right=0.985, bottom=0.20, top=0.94)

    care_bars = ax.bar(
        x - width / 2,
        care,
        width,
        label="CARE",
        color="#D73027",
        edgecolor="black",
        linewidth=1.35,
        hatch="//",
    )
    llm_bars = ax.bar(
        x + width / 2,
        llm_only,
        width,
        label="LLM-only",
        color="#4575B4",
        edgecolor="black",
        linewidth=1.35,
        hatch="\\\\",
    )

    ax.set_xlim(-0.55, len(categories) - 0.45)
    ax.set_ylim(0, 45)
    ax.set_ylabel("Rate (%)", fontsize=9.2)
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=9.0)
    ax.set_yticks([0, 10, 20, 30, 40])
    style_axes(ax)

    legend = ax.legend(
        loc="upper right",
        frameon=True,
        framealpha=1,
        edgecolor="black",
        fontsize=6.8,
        handlelength=1.55,
        borderpad=0.28,
    )
    legend.get_frame().set_linewidth(1.1)

    for bars, values in [(care_bars, care), (llm_bars, llm_only)]:
        for bar, value in zip(bars, values):
            annotate_bar(ax, bar, f"{value:.1f}")

    ax.text(-0.36, 43.0, "3.1x", ha="left", va="center", fontsize=8.1, fontweight="bold")
    ax.text(1, 12.0, "6.2x", ha="center", va="center", fontsize=8.1, fontweight="bold")
    return fig


def draw_ablation_figure():
    labels = ["Full", "No\nRes.", "No\nSem.", "No\nSec.", "No\nR/S/S"]
    validated = np.array([31, 31, 31, 31, 31])
    false_accepts = np.array([0, 121, 0, 1, 125])
    other_extra = np.array([0, 37, 0, 0, 37])
    x = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(3.35, 2.25))
    fig.subplots_adjust(left=0.17, right=0.985, bottom=0.22, top=0.91)

    b1 = ax.bar(
        x,
        validated,
        0.50,
        color="#4575B4",
        edgecolor="black",
        linewidth=1.25,
        hatch="\\\\",
        label="Full-valid",
    )
    b2 = ax.bar(
        x,
        false_accepts,
        0.50,
        bottom=validated,
        color="#D73027",
        edgecolor="black",
        linewidth=1.25,
        hatch="//",
        label="False accept",
    )
    b3 = ax.bar(
        x,
        other_extra,
        0.50,
        bottom=validated + false_accepts,
        color="#BDBDBD",
        edgecolor="black",
        linewidth=1.25,
        hatch="xx",
        label="Other extra",
    )

    totals = validated + false_accepts + other_extra
    for xpos, total in zip(x, totals):
        ax.text(xpos, total + 5, str(int(total)), ha="center", va="bottom", fontsize=7.5)
    for xpos, false_count, base in zip(x, false_accepts, validated):
        if false_count >= 20:
            ax.text(
                xpos,
                base + false_count / 2,
                str(int(false_count)),
                ha="center",
                va="center",
                fontsize=7.1,
                color="white",
                fontweight="bold",
            )

    ax.set_ylim(0, 215)
    ax.set_ylabel("Accepted patches", fontsize=9.0)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.1)
    ax.set_yticks([0, 50, 100, 150, 200])
    style_axes(ax)

    handles = [
        Patch(facecolor="#4575B4", edgecolor="black", hatch="\\\\", label="Full-valid"),
        Patch(facecolor="#D73027", edgecolor="black", hatch="//", label="False"),
        Patch(facecolor="#BDBDBD", edgecolor="black", hatch="xx", label="Other"),
    ]
    legend = ax.legend(
        handles=handles,
        loc="upper left",
        ncol=3,
        frameon=True,
        framealpha=1,
        edgecolor="black",
        fontsize=6.5,
        handlelength=1.4,
        borderpad=0.30,
        columnspacing=0.65,
    )
    legend.get_frame().set_linewidth(1.0)
    return fig


def save_figure(fig, stem: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_IMG_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png", "svg"):
        for directory in (OUT_DIR, PAPER_IMG_DIR):
            path = directory / f"{stem}.{ext}"
            if ext == "png":
                fig.savefig(path, dpi=600, bbox_inches="tight", pad_inches=0, facecolor="white")
            else:
                fig.savefig(path, bbox_inches="tight", pad_inches=0, facecolor="white")
            print(path)


def main() -> None:
    configure_matplotlib()
    yield_fig = draw_yield_figure()
    save_figure(yield_fig, "care_eval_yield")
    plt.close(yield_fig)

    ablation_fig = draw_ablation_figure()
    save_figure(ablation_fig, "care_eval_ablation")
    plt.close(ablation_fig)


if __name__ == "__main__":
    main()
