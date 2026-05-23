"""Draw a detailed CARE architecture figure in the provided reference style.

Run from the repository root:

    python3 docs/figures/care_system_architecture_reference_style.py

Outputs:
    docs/figures/care_system_architecture_reference_style.pdf
    docs/figures/care_system_architecture_reference_style.png
    docs/figures/care_system_architecture_reference_style.svg
"""

from __future__ import annotations

import math
from pathlib import Path
from textwrap import fill

import matplotlib.pyplot as plt
from matplotlib import patheffects as pe
from matplotlib.patches import Arc, Circle, Ellipse, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "figures"


COLORS = {
    "input": "#EAF2FC",
    "analysis": "#EAF8EF",
    "detection": "#F2EAFB",
    "llm": "#FFF4DE",
    "validation": "#EAF2FF",
    "refinement": "#FDECEF",
    "output": "#E8F7F5",
    "tools": "#FFFFFF",
    "panel_border": "#304252",
    "sub_border": "#C9D3DE",
    "text": "#172033",
    "muted": "#5D6675",
    "arrow": "#111827",
    "feedback": "#C83D4C",
    "green": "#1B9A59",
    "blue": "#2D7DD2",
    "purple": "#6A3EA1",
    "orange": "#E68A00",
    "teal": "#0E8C7A",
    "red": "#E84A5F",
    "gray": "#64748B",
}


def setup() -> tuple[plt.Figure, plt.Axes]:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.unicode_minus": False,
        }
    )
    fig, ax = plt.subplots(figsize=(18.6, 11.2))
    ax.set_xlim(0, 18.6)
    ax.set_ylim(0, 11.2)
    ax.axis("off")
    return fig, ax


def round_box(
    ax: plt.Axes,
    x: float,
    y: float,
    w: float,
    h: float,
    fc: str,
    ec: str = COLORS["panel_border"],
    lw: float = 1.05,
    radius: float = 0.08,
    linestyle: str = "-",
    shadow: bool = True,
    z: int = 1,
) -> FancyBboxPatch:
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0.018,rounding_size={radius}",
        facecolor=fc,
        edgecolor=ec,
        linewidth=lw,
        linestyle=linestyle,
        zorder=z,
    )
    if shadow:
        patch.set_path_effects(
            [pe.SimplePatchShadow(offset=(0.7, -0.7), alpha=0.09, rho=0.98), pe.Normal()]
        )
    ax.add_patch(patch)
    return patch


def numbered_title(
    ax: plt.Axes,
    x: float,
    y: float,
    num: int,
    title: str,
    color: str,
    fontsize: float = 9.2,
) -> None:
    ax.add_patch(Circle((x, y), 0.145, facecolor=color, edgecolor="white", linewidth=1.1, zorder=5))
    ax.text(x, y, str(num), color="white", ha="center", va="center", fontsize=8.8, fontweight="bold", zorder=6)
    ax.text(x + 0.25, y, title, ha="left", va="center", fontsize=fontsize, fontweight="bold", color=COLORS["text"], zorder=6)


def subcard(
    ax: plt.Axes,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    body: list[str] | str,
    icon: str | None = None,
    accent: str = COLORS["blue"],
    title_size: float = 7.7,
    body_size: float = 6.7,
    wrap_width: int = 30,
) -> None:
    round_box(ax, x, y, w, h, "white", ec=COLORS["sub_border"], lw=0.85, radius=0.055, shadow=False, z=2)
    icon_space = 0.43 if icon else 0.0
    if icon:
        draw_icon(ax, icon, x + 0.27, y + h - 0.35, 0.34, accent)
    ax.text(
        x + 0.17 + icon_space,
        y + h - 0.18,
        title,
        ha="left",
        va="top",
        fontsize=title_size,
        fontweight="bold",
        color=COLORS["text"],
        zorder=4,
    )
    if isinstance(body, str):
        lines = [body]
    else:
        lines = body
    text = "\n".join(fill(line, width=wrap_width) for line in lines)
    ax.text(
        x + 0.17 + icon_space,
        y + h - 0.50,
        text,
        ha="left",
        va="top",
        fontsize=body_size,
        color="#202938",
        linespacing=1.22,
        zorder=4,
    )


def analysis_card(
    ax: plt.Axes,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    body: list[str],
    icon: str,
    accent: str,
) -> None:
    round_box(ax, x, y, w, h, "white", ec=COLORS["sub_border"], lw=0.85, radius=0.055, shadow=False, z=2)
    draw_icon(ax, icon, x + w / 2, y + h - 0.32, 0.36, accent)
    ax.text(
        x + w / 2,
        y + h - 0.58,
        title,
        ha="center",
        va="top",
        fontsize=5.9,
        fontweight="bold",
        color=COLORS["text"],
        linespacing=1.03,
        zorder=4,
    )
    ax.text(
        x + w / 2,
        y + 0.62,
        "\n".join(body),
        ha="center",
        va="top",
        fontsize=5.25,
        color="#202938",
        linespacing=1.08,
        zorder=4,
    )


def arrow(
    ax: plt.Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    color: str = COLORS["arrow"],
    lw: float = 1.35,
    style: str = "-",
    rad: float = 0.0,
    ms: float = 11,
    z: int = 3,
) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=ms,
            linewidth=lw,
            linestyle=style,
            color=color,
            connectionstyle=f"arc3,rad={rad}",
            shrinkA=3,
            shrinkB=3,
            zorder=z,
        )
    )


def draw_icon(ax: plt.Axes, kind: str, cx: float, cy: float, s: float, color: str) -> None:
    if kind == "file":
        draw_file(ax, cx, cy, s, color)
    elif kind == "gear":
        draw_gear(ax, cx, cy, s, color)
    elif kind == "db":
        draw_database(ax, cx, cy, s, color)
    elif kind == "check":
        draw_check(ax, cx, cy, s, color)
    elif kind == "tree":
        draw_tree(ax, cx, cy, s, color)
    elif kind == "cfg":
        draw_cfg(ax, cx, cy, s, color)
    elif kind == "flow":
        draw_flow(ax, cx, cy, s, color)
    elif kind == "shield":
        draw_shield(ax, cx, cy, s, color)
    elif kind == "goto":
        draw_file(ax, cx, cy, s, color)
        ax.text(cx, cy, "goto", ha="center", va="center", fontsize=4.8, fontweight="bold", color=color, zorder=6)
    elif kind == "lock":
        draw_lock(ax, cx, cy, s, color)
    elif kind == "warn":
        draw_warning(ax, cx, cy, s, color)
    elif kind == "robot":
        draw_robot(ax, cx, cy, s, color)
    elif kind == "wand":
        draw_wand(ax, cx, cy, s, color)
    elif kind == "monitor":
        draw_monitor(ax, cx, cy, s, color)
    elif kind == "flask":
        draw_flask(ax, cx, cy, s, color)
    elif kind == "scale":
        draw_scale(ax, cx, cy, s, color)
    elif kind == "bars":
        draw_bars(ax, cx, cy, s, color)
    elif kind == "loop":
        draw_loop(ax, cx, cy, s, color)
    else:
        ax.add_patch(Circle((cx, cy), s * 0.35, facecolor=color, edgecolor="none", zorder=5))


def draw_file(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    w, h = s * 0.72, s * 0.92
    x, y = cx - w / 2, cy - h / 2
    ax.add_patch(Rectangle((x, y), w, h, facecolor="white", edgecolor=COLORS["panel_border"], linewidth=0.9, zorder=5))
    ax.add_patch(Polygon([[x + w * 0.68, y + h], [x + w, y + h * 0.70], [x + w, y + h]], facecolor="#E9EEF5", edgecolor=COLORS["panel_border"], linewidth=0.7, zorder=6))
    ax.text(cx, cy - h * 0.12, "</>", ha="center", va="center", fontsize=6.2, color=color, fontweight="bold", zorder=6)


def draw_gear(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    for i in range(8):
        angle = i * math.pi / 4
        x = cx + math.cos(angle) * s * 0.34
        y = cy + math.sin(angle) * s * 0.34
        ax.add_patch(Rectangle((x - s * 0.035, y - s * 0.035), s * 0.07, s * 0.07, angle=math.degrees(angle), facecolor=color, edgecolor=color, zorder=5))
    ax.add_patch(Circle((cx, cy), s * 0.30, facecolor="white", edgecolor=color, linewidth=1.3, zorder=5))
    ax.add_patch(Circle((cx, cy), s * 0.11, facecolor="white", edgecolor=color, linewidth=1.0, zorder=6))


def draw_database(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    w, h = s * 0.75, s * 0.78
    ax.add_patch(Rectangle((cx - w / 2, cy - h * 0.35), w, h * 0.7, facecolor="#DDEBFA", edgecolor=color, linewidth=1.0, zorder=5))
    ax.add_patch(Ellipse((cx, cy + h * 0.35), w, h * 0.22, facecolor="#F8FBFF", edgecolor=color, linewidth=1.0, zorder=6))
    ax.add_patch(Ellipse((cx, cy - h * 0.35), w, h * 0.22, facecolor="#DDEBFA", edgecolor=color, linewidth=1.0, zorder=6))


def draw_check(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    ax.add_patch(Rectangle((cx - s * 0.35, cy - s * 0.35), s * 0.70, s * 0.70, facecolor="white", edgecolor=COLORS["panel_border"], linewidth=0.9, zorder=5))
    ax.plot([cx - s * 0.20, cx - s * 0.04, cx + s * 0.22], [cy - s * 0.02, cy - s * 0.18, cy + s * 0.18], color=color, linewidth=1.8, zorder=6)


def draw_tree(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    pts = [(cx, cy + s * 0.32), (cx - s * 0.25, cy - s * 0.05), (cx + s * 0.25, cy - s * 0.05), (cx - s * 0.38, cy - s * 0.35), (cx, cy - s * 0.35), (cx + s * 0.38, cy - s * 0.35)]
    edges = [(0, 1), (0, 2), (1, 3), (1, 4), (2, 5)]
    for a, b in edges:
        ax.plot([pts[a][0], pts[b][0]], [pts[a][1], pts[b][1]], color=COLORS["panel_border"], linewidth=0.9, zorder=5)
    for p in pts:
        ax.add_patch(Circle(p, s * 0.055, facecolor=color, edgecolor="white", linewidth=0.8, zorder=6))


def draw_cfg(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    pts = [(cx, cy + s * 0.30), (cx - s * 0.30, cy), (cx + s * 0.30, cy), (cx - s * 0.30, cy - s * 0.30), (cx + s * 0.30, cy - s * 0.30)]
    for a, b in [(0, 1), (0, 2), (1, 3), (2, 4)]:
        arrow(ax, pts[a], pts[b], color=COLORS["panel_border"], lw=0.8, ms=6, z=5)
    for p in pts:
        ax.add_patch(Rectangle((p[0] - s * 0.06, p[1] - s * 0.045), s * 0.12, s * 0.09, facecolor=color, edgecolor="white", linewidth=0.7, zorder=6))


def draw_flow(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    ax.plot([cx - s * 0.35, cx + s * 0.18], [cy + s * 0.15, cy + s * 0.15], color=color, linewidth=1.4, linestyle="--", zorder=5)
    arrow(ax, (cx + s * 0.05, cy + s * 0.15), (cx + s * 0.33, cy + s * 0.15), color=color, lw=1.2, ms=7, z=5)
    for dx, dy in [(-0.28, -0.22), (0.02, -0.12), (0.30, -0.28)]:
        ax.add_patch(Circle((cx + dx * s, cy + dy * s), s * 0.045, facecolor=color, edgecolor="none", zorder=6))


def draw_shield(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    pts = [(cx, cy + s * 0.42), (cx + s * 0.32, cy + s * 0.25), (cx + s * 0.25, cy - s * 0.20), (cx, cy - s * 0.42), (cx - s * 0.25, cy - s * 0.20), (cx - s * 0.32, cy + s * 0.25)]
    ax.add_patch(Polygon(pts, closed=True, facecolor=color, edgecolor=COLORS["panel_border"], linewidth=0.8, zorder=5))
    ax.plot([cx - s * 0.13, cx - s * 0.02, cx + s * 0.16], [cy, cy - s * 0.13, cy + s * 0.14], color="white", linewidth=1.4, zorder=6)


def draw_lock(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    ax.add_patch(Rectangle((cx - s * 0.30, cy - s * 0.25), s * 0.60, s * 0.45, facecolor="#FFE08A", edgecolor=COLORS["panel_border"], linewidth=0.9, zorder=5))
    ax.add_patch(Arc((cx, cy + s * 0.17), s * 0.42, s * 0.42, theta1=0, theta2=180, color=COLORS["panel_border"], linewidth=1.3, zorder=6))
    ax.add_patch(Circle((cx, cy - s * 0.03), s * 0.045, facecolor=COLORS["panel_border"], edgecolor="none", zorder=6))


def draw_warning(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    pts = [(cx, cy + s * 0.40), (cx + s * 0.38, cy - s * 0.32), (cx - s * 0.38, cy - s * 0.32)]
    ax.add_patch(Polygon(pts, closed=True, facecolor="#FFD166", edgecolor=COLORS["panel_border"], linewidth=0.9, zorder=5))
    ax.text(cx, cy - s * 0.05, "!", ha="center", va="center", fontsize=8.5, fontweight="bold", color=COLORS["panel_border"], zorder=6)


def draw_robot(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    round_box(ax, cx - s * 0.33, cy - s * 0.25, s * 0.66, s * 0.50, "#DCEBFF", ec=COLORS["panel_border"], lw=0.9, radius=0.035, shadow=False, z=5)
    ax.plot([cx, cx], [cy + s * 0.25, cy + s * 0.40], color=COLORS["panel_border"], linewidth=1.0, zorder=6)
    ax.add_patch(Circle((cx, cy + s * 0.44), s * 0.045, facecolor=color, edgecolor="none", zorder=6))
    ax.add_patch(Circle((cx - s * 0.14, cy + s * 0.02), s * 0.045, facecolor=color, edgecolor="none", zorder=6))
    ax.add_patch(Circle((cx + s * 0.14, cy + s * 0.02), s * 0.045, facecolor=color, edgecolor="none", zorder=6))
    ax.plot([cx - s * 0.13, cx + s * 0.13], [cy - s * 0.11, cy - s * 0.11], color=COLORS["panel_border"], linewidth=1.0, zorder=6)


def draw_wand(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    ax.plot([cx - s * 0.28, cx + s * 0.25], [cy - s * 0.28, cy + s * 0.25], color=color, linewidth=2.0, zorder=5)
    for dx, dy in [(0.28, 0.32), (-0.05, 0.35), (0.35, -0.05)]:
        ax.plot([cx + dx * s, cx + dx * s], [cy + (dy - 0.06) * s, cy + (dy + 0.06) * s], color=color, linewidth=1.0, zorder=6)
        ax.plot([cx + (dx - 0.06) * s, cx + (dx + 0.06) * s], [cy + dy * s, cy + dy * s], color=color, linewidth=1.0, zorder=6)


def draw_monitor(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    ax.add_patch(Rectangle((cx - s * 0.38, cy - s * 0.20), s * 0.76, s * 0.50, facecolor="#263243", edgecolor=COLORS["panel_border"], linewidth=0.8, zorder=5))
    ax.text(cx, cy + s * 0.03, "</>", ha="center", va="center", fontsize=6.0, color="white", zorder=6)
    ax.plot([cx, cx], [cy - s * 0.20, cy - s * 0.35], color=COLORS["panel_border"], linewidth=1.0, zorder=6)
    ax.plot([cx - s * 0.22, cx + s * 0.22], [cy - s * 0.35, cy - s * 0.35], color=COLORS["panel_border"], linewidth=1.0, zorder=6)


def draw_flask(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    ax.add_patch(Rectangle((cx - s * 0.08, cy + s * 0.08), s * 0.16, s * 0.32, facecolor="white", edgecolor=COLORS["panel_border"], linewidth=0.9, zorder=5))
    pts = [(cx - s * 0.08, cy + s * 0.08), (cx - s * 0.30, cy - s * 0.35), (cx + s * 0.30, cy - s * 0.35), (cx + s * 0.08, cy + s * 0.08)]
    ax.add_patch(Polygon(pts, closed=True, facecolor="#B9F0E5", edgecolor=COLORS["panel_border"], linewidth=0.9, zorder=5))
    ax.plot([cx - s * 0.21, cx + s * 0.21], [cy - s * 0.16, cy - s * 0.16], color=color, linewidth=1.1, zorder=6)


def draw_scale(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    ax.plot([cx, cx], [cy - s * 0.32, cy + s * 0.35], color=COLORS["panel_border"], linewidth=1.1, zorder=5)
    ax.plot([cx - s * 0.38, cx + s * 0.38], [cy + s * 0.18, cy + s * 0.18], color=COLORS["panel_border"], linewidth=1.1, zorder=5)
    for dx in [-0.26, 0.26]:
        ax.add_patch(Polygon([(cx + dx * s, cy + s * 0.14), (cx + (dx - 0.14) * s, cy - s * 0.12), (cx + (dx + 0.14) * s, cy - s * 0.12)], closed=True, facecolor="#FFF2BF", edgecolor=color, linewidth=0.9, zorder=6))


def draw_bars(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    heights = [0.32, 0.55, 0.78]
    for i, h in enumerate(heights):
        ax.add_patch(Rectangle((cx - s * 0.34 + i * s * 0.24, cy - s * 0.35), s * 0.13, s * h, facecolor=color, edgecolor=COLORS["panel_border"], linewidth=0.7, zorder=5))


def draw_loop(ax: plt.Axes, cx: float, cy: float, s: float, color: str) -> None:
    ax.add_patch(Arc((cx, cy), s * 0.68, s * 0.68, theta1=40, theta2=330, color=color, linewidth=1.5, zorder=5))
    arrow(ax, (cx + s * 0.18, cy + s * 0.31), (cx + s * 0.33, cy + s * 0.18), color=color, lw=1.1, ms=7, z=6)


def build_figure() -> plt.Figure:
    fig, ax = setup()

    # Main panels.
    p1 = (0.15, 5.82, 2.35, 5.15)
    p2 = (2.70, 5.82, 7.10, 5.15)
    p3 = (10.00, 6.25, 4.15, 4.72)
    p4 = (14.40, 5.82, 4.05, 5.15)
    p5 = (1.55, 3.44, 12.50, 1.92)
    p6 = (3.70, 1.64, 9.75, 1.46)
    p7 = (15.15, 2.25, 3.15, 3.18)

    for x, y, w, h, color in [
        (*p1, COLORS["input"]),
        (*p2, COLORS["analysis"]),
        (*p3, COLORS["detection"]),
        (*p4, COLORS["llm"]),
        (*p5, COLORS["validation"]),
        (*p6, COLORS["refinement"]),
        (*p7, COLORS["output"]),
    ]:
        round_box(ax, x, y, w, h, color, lw=1.0, radius=0.09)

    numbered_title(ax, p1[0] + 0.22, p1[1] + p1[3] - 0.24, 1, "Target Codebase", COLORS["blue"], 8.7)
    numbered_title(ax, p2[0] + 1.42, p2[1] + p2[3] - 0.24, 2, "Program Understanding & Context Construction", COLORS["green"], 8.5)
    numbered_title(ax, p3[0] + 0.58, p3[1] + p3[3] - 0.24, 3, "Refactoring Opportunity Detection", COLORS["purple"], 8.3)
    numbered_title(ax, p4[0] + 0.48, p4[1] + p4[3] - 0.24, 4, "LLM-based Refactoring Engine", COLORS["orange"], 8.5)
    numbered_title(ax, p5[0] + 4.60, p5[1] + p5[3] - 0.18, 5, "Correctness & Security Validator", COLORS["blue"], 8.6)
    numbered_title(ax, p6[0] + 3.20, p6[1] + p6[3] - 0.18, 6, "Feedback-guided Iterative Refinement", COLORS["red"], 8.5)
    numbered_title(ax, p7[0] + 0.70, p7[1] + p7[3] - 0.22, 7, "Output & Reporting", COLORS["teal"], 8.5)

    draw_target_codebase(ax, *p1)
    draw_program_understanding(ax, *p2)
    draw_detectors(ax, *p3)
    draw_llm_engine(ax, *p4)
    draw_validator(ax, *p5)
    draw_refinement(ax, *p6)
    draw_output(ax, *p7)
    draw_tools_and_legend(ax)
    draw_pipeline_edges(ax, p1, p2, p3, p4, p5, p6, p7)

    return fig


def draw_target_codebase(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    items = [
        ("file", "C/C++ Source\nFiles", COLORS["teal"]),
        ("gear", "Build System\n(Make/CMake)", COLORS["gray"]),
        ("db", "compile_commands.json\n(optional)", COLORS["blue"]),
        ("check", "Tests\n(unit/integration)", COLORS["green"]),
    ]
    row_h = 1.02
    top = y + h - 0.70
    for idx, (icon, label, color) in enumerate(items):
        yy = top - idx * row_h - row_h + 0.03
        round_box(ax, x + 0.12, yy, w - 0.24, row_h - 0.04, "white", ec=COLORS["sub_border"], lw=0.65, radius=0.055, shadow=False)
        draw_icon(ax, icon, x + 0.48, yy + row_h * 0.47, 0.55, color)
        ax.text(x + 0.88, yy + row_h * 0.47, label, ha="left", va="center", fontsize=7.2, color=COLORS["text"], linespacing=1.15)


def draw_program_understanding(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    top_y = y + h - 2.10
    card_w = (w - 0.36) / 5
    cards = [
        ("tree", "AST\nParsing", ["Functions, variables,", "statements, calls,", "labels, gotos"], COLORS["blue"]),
        ("cfg", "CFG\nConstruction", ["Basic blocks,", "control edges,", "branches, gotos"], COLORS["gray"]),
        ("tree", "Call\nGraph", ["Interprocedural", "caller-callee", "relations"], COLORS["purple"]),
        ("flow", "Data-flow\nAnalysis", ["Defs/uses, aliases,", "sources/sinks,", "resource vars"], COLORS["orange"]),
        ("shield", "Security Knowledge\n& Patterns", ["Resource lifecycle,", "error handling,", "security patterns"], COLORS["teal"]),
    ]
    for i, (icon, title, body, color) in enumerate(cards):
        cx = x + 0.18 + i * card_w
        analysis_card(ax, cx, top_y, card_w - 0.02, 1.65, title, body, icon=icon, accent=color)

    arrow(ax, (x + w / 2, top_y), (x + w / 2, y + 2.57), lw=1.15, ms=10)
    graph_x, graph_y, graph_w, graph_h = x + 0.20, y + 0.15, w - 0.40, 2.35
    round_box(ax, graph_x, graph_y, graph_w, graph_h, "white", ec=COLORS["sub_border"], lw=0.8, radius=0.055, shadow=False)
    ax.text(graph_x + graph_w / 2, graph_y + graph_h - 0.22, "Security-aware Contextual Refactoring Graph", ha="center", va="top", fontsize=8.5, fontweight="bold", color=COLORS["text"])

    # Legend inside graph panel.
    lx, ly = graph_x + 0.36, graph_y + 1.45
    legend = [
        ("Function", COLORS["blue"], "dot"),
        ("Basic Block", "#82C46C", "dot"),
        ("Call Edge", COLORS["arrow"], "line"),
        ("Control Edge", COLORS["arrow"], "line"),
        ("Data Edge", COLORS["arrow"], "dash"),
        ("Goto Edge", COLORS["feedback"], "dash"),
    ]
    for i, (label, color, kind) in enumerate(legend):
        yy = ly - i * 0.28
        if kind == "dot":
            ax.add_patch(Circle((lx, yy), 0.055, facecolor=color, edgecolor=COLORS["panel_border"], linewidth=0.5, zorder=5))
        else:
            ax.plot([lx - 0.08, lx + 0.12], [yy, yy], color=color, linewidth=1.1, linestyle="--" if kind == "dash" else "-", zorder=5)
            arrow(ax, (lx + 0.05, yy), (lx + 0.20, yy), color=color, lw=0.8, ms=6, z=5)
        ax.text(lx + 0.25, yy, label, ha="left", va="center", fontsize=5.9, color=COLORS["text"])

    draw_context_network(ax, graph_x + 3.10, graph_y + 1.02, 1.45)

    rx = graph_x + graph_w - 2.55
    right_items = [
        ("Resource State (alloc/free/lock)", "lock"),
        ("Error-handling Path", "flow"),
        ("Global Var & Side Effects", "cfg"),
        ("Return Status & Conditions", "shield"),
        ("Function Summary", "file"),
    ]
    for i, (label, icon) in enumerate(right_items):
        yy = graph_y + 1.55 - i * 0.32
        draw_icon(ax, icon, rx, yy, 0.25, COLORS["gray"])
        ax.text(rx + 0.28, yy, label, ha="left", va="center", fontsize=5.8, color=COLORS["text"])


def draw_context_network(ax: plt.Axes, cx: float, cy: float, s: float) -> None:
    pts = [
        (cx - 0.85 * s, cy + 0.45 * s),
        (cx - 0.45 * s, cy + 0.10 * s),
        (cx - 0.78 * s, cy - 0.42 * s),
        (cx - 0.15 * s, cy + 0.45 * s),
        (cx + 0.15 * s, cy - 0.25 * s),
        (cx + 0.52 * s, cy + 0.35 * s),
        (cx + 0.90 * s, cy - 0.05 * s),
        (cx + 0.58 * s, cy - 0.62 * s),
    ]
    edges = [(0, 1), (1, 2), (1, 3), (3, 4), (4, 5), (5, 6), (4, 7), (6, 7)]
    for a, b in edges:
        arrow(ax, pts[a], pts[b], color=COLORS["arrow"], lw=0.8, ms=6, z=4)
    ax.add_patch(Ellipse((cx, cy), 2.2 * s, 1.65 * s, facecolor="none", edgecolor=COLORS["feedback"], linewidth=0.8, linestyle=(0, (3, 3)), zorder=3))
    for i, (px, py) in enumerate(pts):
        color = COLORS["blue"] if i in {1, 3, 4} else "#82C46C"
        ax.add_patch(Circle((px, py), 0.075, facecolor=color, edgecolor=COLORS["panel_border"], linewidth=0.5, zorder=6))


def draw_detectors(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    items = [
        ("goto", "Goto-based Redundancy\nDetector", "Duplicated cleanup, unnecessary gotos, repeated error handling", COLORS["red"]),
        ("lock", "Resource Imbalance\nDetector", "Missing/double free, close, unlock; early returns", COLORS["orange"]),
        ("check", "Duplicate Check\nDetector", "Repeated null/bounds/status checks with no intervening mutation", COLORS["green"]),
        ("warn", "Dead/Unreachable Code\nDetector", "Unreachable code, redundant branches, unused labels", COLORS["gray"]),
        ("shield", "General Security Smell\nDetector", "Unchecked returns, unsafe APIs, inconsistent error handling", COLORS["purple"]),
    ]
    top = y + h - 0.64
    row_h = 0.70
    for i, (icon, title, body, color) in enumerate(items):
        yy = top - (i + 1) * row_h
        subcard(ax, x + 0.14, yy, w - 0.28, row_h - 0.03, title, body, icon=icon, accent=color, title_size=6.3, body_size=5.45, wrap_width=42)
    rank_y = y + 0.13
    round_box(ax, x + 0.14, rank_y, w - 0.28, 0.43, "#FFF6FB", ec="#E5B8C4", lw=0.75, radius=0.055, shadow=False)
    draw_icon(ax, "flow", x + 0.42, rank_y + 0.22, 0.34, COLORS["red"])
    ax.text(x + 0.75, rank_y + 0.22, "Refactoring Opportunities (ranked)", ha="left", va="center", fontsize=7.3, fontweight="bold", color=COLORS["purple"])


def draw_llm_engine(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    top = y + h - 0.65
    subcard(ax, x + 0.14, top - 2.10, w - 0.28, 2.00, "4.1  LLM Refactoring Planner", ["Understand intent & context", "Identify invariants & constraints", "Plan refactoring strategy", "Define validation strategy"], icon="robot", accent=COLORS["purple"], title_size=7.2, body_size=6.1, wrap_width=42)
    plan_y = top - 2.62
    round_box(ax, x + 0.42, plan_y, w - 0.84, 0.42, "#FFFDF7", ec="#E6D3A7", lw=0.7, radius=0.055, shadow=False)
    draw_icon(ax, "file", x + 0.70, plan_y + 0.21, 0.30, COLORS["orange"])
    ax.text(x + 1.02, plan_y + 0.21, "Refactoring Plan (JSON)", ha="left", va="center", fontsize=6.7, color=COLORS["orange"], fontweight="bold")
    arrow(ax, (x + w / 2, plan_y), (x + w / 2, plan_y - 0.25), lw=1.05, ms=9)
    subcard(ax, x + 0.14, y + 0.25, w - 0.28, 1.78, "4.2  LLM Patch Generator", ["Generate multiple patch candidates", "(conservative / aggressive)", "Unified diff output"], icon="wand", accent=COLORS["orange"], title_size=7.2, body_size=6.1, wrap_width=42)
    cand_y = y + 0.38
    for i, label in enumerate(["Candidate 1", "Candidate 2", "...", "Candidate N"]):
        cx = x + 0.70 + i * 0.86
        if label == "...":
            ax.text(cx, cand_y + 0.20, "...", ha="center", va="center", fontsize=14, color=COLORS["text"])
        else:
            draw_icon(ax, "file", cx, cand_y + 0.24, 0.34, COLORS["teal"])
            ax.text(cx, cand_y - 0.12, label, ha="center", va="center", fontsize=5.7, color=COLORS["text"])


def draw_validator(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    card_y = y + 0.20
    card_h = h - 0.52
    n = 6
    card_w = (w - 0.48) / n
    items = [
        ("monitor", "Compile Check", ["Build success", "No compiler errors"], COLORS["blue"]),
        ("flask", "Test Execution", ["Unit/integration tests", "Behavior preserved"], COLORS["teal"]),
        ("warn", "Static Analysis", ["clang-tidy/cppcheck", "scan-build optional", "warnings recorded"], COLORS["red"]),
        ("lock", "Resource Consistency", ["alloc/free match", "lock/unlock match", "no leaks/double free"], COLORS["purple"]),
        ("scale", "Semantic Equivalence", ["API/signature check", "return behavior", "side-effect check"], COLORS["orange"]),
        ("shield", "Security Regression", ["no removed safety checks", "no unsafe API introduced", "error paths preserved"], COLORS["green"]),
    ]
    for i, (icon, title, body, color) in enumerate(items):
        cx = x + 0.20 + i * card_w
        subcard(ax, cx, card_y, card_w - 0.02, card_h, title, body, icon=icon, accent=color, title_size=5.9, body_size=5.1, wrap_width=24)


def draw_refinement(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    card_y = y + 0.22
    card_h = h - 0.52
    card_w = 1.78
    items = [
        ("flow", "Validation Feedback", ["Failure logs", "Counterexamples", "Root causes"], COLORS["blue"]),
        ("robot", "Plan Revision", ["Adjust strategy", "Strengthen constraints", "Narrow patch scope"], COLORS["purple"]),
        ("file", "Patch Repair", ["Regenerate patch", "Smaller/safer changes", "Multiple candidates"], COLORS["orange"]),
        ("loop", "Re-validate", ["Iterate up to N times", "until success or stop"], COLORS["teal"]),
    ]
    xs = [x + 0.22, x + 2.75, x + 5.25, x + 7.65]
    for idx, (icon, title, body, color) in enumerate(items):
        subcard(ax, xs[idx], card_y, card_w, card_h, title, body, icon=icon, accent=color, title_size=5.8, body_size=5.0, wrap_width=22)
        if idx < len(items) - 1:
            arrow(ax, (xs[idx] + card_w, card_y + card_h / 2), (xs[idx + 1], card_y + card_h / 2), lw=1.05, ms=9)


def draw_output(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    subcard(ax, x + 0.14, y + h - 1.45, w - 0.28, 0.95, "Selected Patch", ["Unified diff", "Explanation"], icon="file", accent=COLORS["green"], title_size=6.8, body_size=5.8, wrap_width=30)
    subcard(ax, x + 0.14, y + 0.35, w - 0.28, 1.45, "Comprehensive Report", ["Opportunities", "Before/after summary", "Validation results", "Security impact", "Performance estimate", "Traceability"], icon="bars", accent=COLORS["blue"], title_size=6.8, body_size=5.5, wrap_width=32)


def draw_tools_and_legend(ax: plt.Axes) -> None:
    tools = (0.35, 0.22, 11.05, 1.10)
    legend = (11.50, 0.22, 6.65, 1.10)
    round_box(ax, *tools, "white", ec="#606A78", lw=0.9, radius=0.055, linestyle=(0, (5, 4)), shadow=False)
    round_box(ax, *legend, "white", ec="#606A78", lw=0.9, radius=0.055, linestyle=(0, (5, 4)), shadow=False)
    ax.text(tools[0] + 0.35, tools[1] + tools[3] - 0.28, "Knowledge & Tools", ha="left", va="center", fontsize=7.6, fontweight="bold", color=COLORS["text"])
    tool_items = [
        ("file", "C/C++\nGrammar"),
        ("shield", "Security\nPatterns"),
        ("file", "API Semantics\n& Specs"),
        ("gear", "Build & Test\nTools"),
        ("gear", "Static Analysis\nTools"),
        ("flow", "Version Control\n(Git)"),
        ("robot", "LLM\n(cloud/local)"),
    ]
    for i, (icon, label) in enumerate(tool_items):
        cx = tools[0] + 2.35 + i * 1.15
        draw_icon(ax, icon, cx, tools[1] + 0.64, 0.34, COLORS["gray"])
        ax.text(cx, tools[1] + 0.23, label, ha="center", va="center", fontsize=5.7, color=COLORS["text"], linespacing=1.1)

    ax.text(legend[0] + 0.85, legend[1] + legend[3] - 0.26, "Legend", ha="left", va="center", fontsize=7.6, fontweight="bold", color=COLORS["text"])
    lx = legend[0] + 0.70
    ly = legend[1] + 0.67
    arrow(ax, (lx, ly), (lx + 0.35, ly), lw=1.0, ms=8)
    ax.text(lx + 0.55, ly, "Data/control flow", fontsize=5.8, va="center", color=COLORS["text"])
    ax.plot([lx, lx + 0.35], [ly - 0.28, ly - 0.28], color=COLORS["feedback"], linestyle=(0, (4, 3)), linewidth=1.2)
    arrow(ax, (lx + 0.18, ly - 0.28), (lx + 0.35, ly - 0.28), color=COLORS["feedback"], lw=1.0, ms=7)
    ax.text(lx + 0.55, ly - 0.28, "Feedback loop", fontsize=5.8, va="center", color=COLORS["text"])
    arrow(ax, (lx, ly - 0.56), (lx + 0.35, ly - 0.56), lw=1.8, ms=9)
    ax.text(lx + 0.55, ly - 0.56, "Pipeline flow", fontsize=5.8, va="center", color=COLORS["text"])

    color_items = [
        ("Input", COLORS["blue"]),
        ("Analysis", COLORS["green"]),
        ("Detection", COLORS["purple"]),
        ("LLM Engine", COLORS["orange"]),
        ("Validation", COLORS["blue"]),
        ("Refinement", COLORS["red"]),
        ("Output", COLORS["teal"]),
    ]
    for i, (label, color) in enumerate(color_items):
        col = 0 if i < 4 else 1
        row = i if i < 4 else i - 4
        cx = legend[0] + 3.25 + col * 1.55
        cy = legend[1] + 0.82 - row * 0.24
        ax.add_patch(Circle((cx, cy), 0.055, facecolor=color, edgecolor="white", linewidth=0.5, zorder=5))
        ax.text(cx + 0.16, cy, label, ha="left", va="center", fontsize=5.8, color=COLORS["text"])


def draw_pipeline_edges(ax: plt.Axes, p1, p2, p3, p4, p5, p6, p7) -> None:
    # Main top pipeline.
    arrow(ax, (p1[0] + p1[2], p1[1] + p1[3] * 0.52), (p2[0], p2[1] + p2[3] * 0.52), lw=1.7, ms=13)
    arrow(ax, (p2[0] + p2[2], p2[1] + p2[3] * 0.52), (p3[0], p3[1] + p3[3] * 0.52), lw=1.7, ms=13)
    arrow(ax, (p3[0] + p3[2], p3[1] + p3[3] * 0.52), (p4[0], p4[1] + p4[3] * 0.52), lw=1.7, ms=13)

    # Detection and candidate flow into validator.
    arrow(ax, (p2[0] + p2[2] * 0.48, p2[1]), (p5[0] + p5[2] * 0.33, p5[1] + p5[3]), lw=1.35, ms=12)
    arrow(ax, (p3[0] + p3[2] * 0.56, p3[1]), (p5[0] + p5[2] * 0.82, p5[1] + p5[3]), lw=1.35, ms=12)
    arrow(ax, (p4[0] + p4[2] * 0.06, p4[1] + 1.10), (p5[0] + p5[2], p5[1] + p5[3] * 0.55), lw=1.7, ms=13, rad=0.0)

    # Validator pass to output.
    pass_x = p5[0] + p5[2] + 0.45
    pass_y = p5[1] + p5[3] * 0.54
    ax.add_patch(Circle((pass_x, pass_y), 0.15, facecolor="#F0FFF4", edgecolor=COLORS["green"], linewidth=1.2, zorder=6))
    ax.text(pass_x, pass_y, "✓", ha="center", va="center", fontsize=11, color=COLORS["green"], zorder=7)
    ax.text(pass_x + 0.16, pass_y + 0.22, "Pass", ha="left", va="center", fontsize=7.0, color=COLORS["green"], fontweight="bold")
    arrow(ax, (p5[0] + p5[2], pass_y), (pass_x - 0.17, pass_y), lw=1.45, ms=12)
    arrow(ax, (pass_x + 0.17, pass_y), (p7[0], pass_y), lw=1.45, ms=12)

    # Validator fail into refinement.
    fail_x = p5[0] + p5[2] * 0.63
    arrow(ax, (fail_x, p5[1]), (fail_x, p6[1] + p6[3]), color=COLORS["feedback"], lw=1.4, ms=12)
    ax.text(fail_x + 0.18, p5[1] - 0.12, "Fail", ha="left", va="center", fontsize=7.0, color=COLORS["feedback"], fontweight="bold")
    arrow(ax, (p6[0] + p6[2] - 0.35, p6[1] + p6[3]), (p4[0] + 0.50, p4[1] + 2.00), color=COLORS["feedback"], lw=1.15, style=(0, (4, 3)), ms=10, rad=0.22)
    arrow(ax, (p4[0] + 0.58, p4[1] + 0.98), (p4[0] + 0.58, p4[1] + 0.45), color=COLORS["feedback"], lw=1.0, style=(0, (4, 3)), ms=8)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig = build_figure()
    for ext in ("pdf", "png", "svg"):
        path = OUT_DIR / f"care_system_architecture_reference_style.{ext}"
        if ext == "png":
            fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
        else:
            fig.savefig(path, bbox_inches="tight", facecolor="white")
        print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
