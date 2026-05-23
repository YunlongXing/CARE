"""Draw the motivating example flow figure for the CARE paper."""

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


def draw_box(ax, x, y, w, h, title, lines, fill, edge="#2F3A45"):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.035,rounding_size=0.055",
        facecolor=fill,
        edgecolor=edge,
        linewidth=1.15,
        zorder=2,
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2,
        y + h - 0.09,
        title,
        ha="center",
        va="top",
        fontsize=8.6,
        fontweight="bold",
        color="#111827",
    )
    ax.text(
        x + w / 2,
        y + h - 0.30,
        "\n".join(lines),
        ha="center",
        va="top",
        fontsize=8.2,
        linespacing=1.05,
        color="#111827",
    )


def draw_arrow(ax, start, end, color="#111827", dashed=False, rad=0.0):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=9,
            linewidth=1.25,
            color=color,
            linestyle=(0, (4, 3)) if dashed else "-",
            connectionstyle=f"arc3,rad={rad}",
            shrinkA=5,
            shrinkB=5,
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
    fig, ax = plt.subplots(figsize=(5.2, 1.55))
    ax.set_xlim(0, 5.2)
    ax.set_ylim(0, 1.55)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

    draw_box(
        ax,
        0.05,
        0.35,
        1.12,
        0.86,
        "Original",
        ["3 return paths", "duplicate cleanup"],
        "#E8F1FA",
    )
    draw_box(
        ax,
        1.42,
        0.35,
        1.18,
        0.86,
        "Context",
        ["free(p)", "lock release", "return codes"],
        "#EDF7EE",
        edge="#2F6F4E",
    )
    draw_box(
        ax,
        2.86,
        0.35,
        1.12,
        0.86,
        "Patch",
        ["ret variable", "single out label"],
        "#FFF4DE",
        edge="#9A6A00",
    )
    draw_box(
        ax,
        4.24,
        0.35,
        0.91,
        0.86,
        "Accept",
        ["tests pass", "gates pass"],
        "#EAF7F7",
        edge="#187E7E",
    )

    draw_arrow(ax, (1.17, 0.78), (1.42, 0.78))
    draw_arrow(ax, (2.60, 0.78), (2.86, 0.78))
    draw_arrow(ax, (3.98, 0.78), (4.24, 0.78))

    ax.text(
        2.60,
        0.12,
        "Safety condition: preserve cleanup order, do_work side effects, and return values",
        ha="center",
        va="center",
        fontsize=8.4,
        color="#B42335",
    )
    draw_arrow(ax, (2.60, 0.25), (2.60, 0.35), color="#B42335", dashed=True)

    return fig


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_IMG_DIR.mkdir(parents=True, exist_ok=True)
    fig = build()
    for ext in ("pdf", "png", "svg"):
        for directory in (OUT_DIR, PAPER_IMG_DIR):
            path = directory / f"care_motivating_example_flow.{ext}"
            if ext == "png":
                fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0, facecolor="white")
            else:
                fig.savefig(path, bbox_inches="tight", pad_inches=0, facecolor="white")
            print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
