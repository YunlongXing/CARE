"""Generate a clear single-column motivating-example code figure."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "figures"
PAPER_IMG_DIR = ROOT / "docs" / "paper" / "img"


ORIGINAL = [
    "if (err1) {",
    "    if (p) free(p);",
    "    lock_release(&lock);",
    "    return -1;",
    "}",
    "",
    "if (err2) {",
    "    do_work();",
    "    if (p) free(p);",
    "    lock_release(&lock);",
    "    return -2;",
    "}",
    "",
    "do_work();",
    "if (p) free(p);",
    "lock_release(&lock);",
    "return 0;",
]


REFACTORED = [
    "int ret = 0;",
    "",
    "if (err1) {",
    "    ret = -1;",
    "    goto out;",
    "}",
    "",
    "if (err2) {",
    "    do_work();",
    "    ret = -2;",
    "    goto out;",
    "}",
    "",
    "do_work();",
    "out:",
    "    if (p) free(p);",
    "    lock_release(&lock);",
    "    return ret;",
]


def font_family(preferred: list[str]) -> list[str]:
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in preferred:
        if name in available:
            return [name]
    return preferred


def draw_code_line(ax, x: float, y: float, text: str, *, fontsize: float) -> None:
    """Draw one monospaced code line as vector text."""
    if not text:
        return
    ax.text(
        x,
        y,
        text,
        ha="left",
        va="center",
        fontsize=fontsize,
        family=font_family(["DejaVu Sans Mono", "Courier New", "Menlo"]),
        color="#111827",
    )


def draw_code_block(ax, x: float, y_top: float, subfigure_label: str, lines: list[str]) -> None:
    code_x = x
    line_h = 0.106
    code_pad_top = 0.025
    fontsize = 5.35

    ax.text(
        x + 0.70,
        0.08,
        subfigure_label,
        ha="center",
        va="bottom",
        fontsize=7.2,
        family=font_family(["Times New Roman", "Times", "DejaVu Serif"]),
        color="#111827",
    )

    block_y_top = y_top
    for idx, line in enumerate(lines, start=1):
        y = block_y_top - code_pad_top - (idx - 0.5) * line_h
        draw_code_line(ax, code_x, y, line, fontsize=fontsize)


def build():
    plt.rcParams.update(
        {
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "font.family": font_family(["Times New Roman", "Times", "DejaVu Serif"]),
        }
    )
    fig, ax = plt.subplots(figsize=(3.35, 2.45))
    ax.set_xlim(0, 3.35)
    ax.set_ylim(0, 2.45)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

    draw_code_block(ax, 0.10, 2.36, "(a)", ORIGINAL)
    draw_code_block(ax, 1.76, 2.36, "(b)", REFACTORED)
    return fig


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_IMG_DIR.mkdir(parents=True, exist_ok=True)
    fig = build()
    for ext in ("pdf", "png", "svg"):
        for directory in (OUT_DIR, PAPER_IMG_DIR):
            path = directory / f"care_motivating_code_comparison.{ext}"
            if ext == "png":
                fig.savefig(path, dpi=600, facecolor="white")
            else:
                fig.savefig(path, facecolor="white")
            print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
