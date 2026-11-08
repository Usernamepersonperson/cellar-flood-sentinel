"""Result figures: docs/figure.png (1600x900) and docs/post-figure.png (960x700)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch

BG, GRID, TEXT, MUTED = "#16213A", "#2A3750", "#F8FAFC", "#A9B6CB"
ACCENT, GREEN, AMBER, RED, GREY, VIOLET = "#38BDF8", "#34D399", "#FBBF24", "#F87171", "#64748B", "#A78BFA"
BADGE = {0: GREY, 1: GREEN, 2: AMBER, 3: RED}
BADGE_TEXT = {0: "no mapped runoff", 1: "depth 0-0.1 m", 2: "depth 0.1-0.25 m", 3: "depth > 0.25 m"}


def _font() -> str:
    names = {f.name for f in font_manager.fontManager.ttflist}
    return "Segoe UI" if "Segoe UI" in names else "DejaVu Sans"


def _style(ax) -> None:
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=11)
    ax.grid(color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def _timeline(ax_rain, ax_lvl, ev: dict, window: pd.Series, t_start) -> None:
    sim = ev["sim"]
    t0 = t_start - pd.Timedelta(minutes=45)
    t1 = (ev["t_wet"] or window.index[-1]) + pd.Timedelta(minutes=80)
    w = window[t0:t1]
    ax_rain.bar(w.index - pd.Timedelta(minutes=5), w.to_numpy() * 6, width=pd.Timedelta(minutes=9), color=ACCENT, align="center")
    ax_rain.set_ylabel("rain, mm/h", color=MUTED, fontsize=12)
    ax_rain.set_ylim(0, float(w.max()) * 6 * 1.75)
    s = sim[t0:t1]
    ax_lvl.plot(s.index, s["level_mm"], color=VIOLET, lw=3)
    ax_lvl.fill_between(s.index, 0, s["level_mm"], color=VIOLET, alpha=0.25)
    ax_lvl.set_ylabel("cellar water, mm (simulated)", color=MUTED, fontsize=12)
    ax_lvl.set_ylim(bottom=0)
    for ax in (ax_rain, ax_lvl):
        _style(ax)
        ax.set_xlim(t0, t1)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))


def _lines(ax_rain, ax_lvl, marks: list, t_wet) -> None:
    """marks: (time, colour, linestyle, legend label)."""
    handles = []
    for t, col, ls, lab in marks:
        if t is None:
            continue
        for ax in (ax_rain, ax_lvl):
            ax.axvline(t, color=col, lw=2.5, ls=ls)
        handles.append(plt.Line2D([0], [0], color=col, lw=2.5, ls=ls, label=lab))
    if t_wet is not None:
        for ax in (ax_rain, ax_lvl):
            ax.axvline(t_wet, color=RED, lw=3)
        handles.append(plt.Line2D([0], [0], color=RED, lw=3, label="sensor sees water (alarm)"))
    ax_rain.legend(handles=handles, loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=TEXT, fontsize=11)


def _lead_arrow(ax, t_from, t_to, y: float, color: str, fs: float = 14) -> None:
    lead = (t_to - t_from).total_seconds() / 60
    ax.annotate("", xy=(t_to, y), xytext=(t_from, y), arrowprops={"arrowstyle": "<->", "color": color, "lw": 2.2})
    mid = t_from + (t_to - t_from) / 2
    ax.text(mid, y + ax.get_ylim()[1] * 0.04, f"{lead:+.0f} min" + (" late" if lead < 0 else " early"), color=color, fontsize=fs, fontweight="bold", ha="center")


def _badge(fig, cls: int, x: float = 0.665, y: float = 0.915, w: float = 0.31, h: float = 0.07) -> None:
    box = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.012", transform=fig.transFigure,
                         facecolor=BADGE[cls], edgecolor="none", zorder=5)
    fig.add_artist(box)
    fig.text(x + w / 2, y + h * 0.66, f"Runoff hazard class {cls}" if cls else "Runoff hazard: none", color=BG,
             fontsize=13, fontweight="bold", ha="center", va="center", zorder=6)
    fig.text(x + w / 2, y + h * 0.28, BADGE_TEXT[cls], color=BG, fontsize=11, ha="center", va="center", zorder=6)


def post_figure(path: Path, ev: dict, window: pd.Series, site_label: str, cls: int, title: str, subtitle: str,
                marks: list) -> None:
    plt.rcParams["font.family"] = _font()
    fig = plt.figure(figsize=(9.6, 7.0), dpi=100, facecolor=BG)
    ax1 = fig.add_axes([0.10, 0.50, 0.87, 0.34])
    ax2 = fig.add_axes([0.10, 0.10, 0.87, 0.34], sharex=ax1)
    times = [m[0] for m in marks if m[0] is not None]
    _timeline(ax1, ax2, ev, window, min(times + [ev["t_wet"]]))
    _lines(ax1, ax2, marks, ev["t_wet"])
    plt.setp(ax1.get_xticklabels(), visible=False)
    ax2.set_xlabel("time, UTC", color=MUTED, fontsize=12)
    ymax = ax2.get_ylim()[1]
    for i, (t, col, _ls, _lab) in enumerate(marks):
        if t is not None and ev["t_wet"] is not None:
            _lead_arrow(ax2, t, ev["t_wet"], ymax * (0.80 - 0.28 * i), col, fs=15)
    fig.text(0.04, 0.955, title, color=TEXT, fontsize=14, fontweight="bold", va="center")
    fig.text(0.04, 0.915, subtitle, color=MUTED, fontsize=10.5, va="center")
    fig.text(0.04, 0.025, site_label, color=MUTED, fontsize=10.5, va="center")
    _badge(fig, cls)
    fig.savefig(path, dpi=100, facecolor=BG)
    plt.close(fig)


def main_figure(path: Path, ev: dict, window: pd.Series, site_label: str, cls: int, marks: list,
                storm_rows: list[dict], season_rows: list[dict]) -> None:
    plt.rcParams["font.family"] = _font()
    fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=BG)
    ax1 = fig.add_axes([0.06, 0.52, 0.48, 0.32])
    ax2 = fig.add_axes([0.06, 0.10, 0.48, 0.32], sharex=ax1)
    times = [m[0] for m in marks if m[0] is not None]
    _timeline(ax1, ax2, ev, window, min(times + [ev["t_wet"]]))
    _lines(ax1, ax2, marks, ev["t_wet"])
    plt.setp(ax1.get_xticklabels(), visible=False)
    ax2.set_xlabel("time, UTC", color=MUTED, fontsize=12)
    fig.text(0.04, 0.955, "Cellar flood early warning: rain trigger vs sensor-only alarm", color=TEXT, fontsize=19, fontweight="bold")
    fig.text(0.04, 0.915, f"{site_label} | open data: MeteoSwiss 10-min rain (UTC), hazard map FOEN/BAFU | cellar water level SIMULATED",
             color=MUTED, fontsize=11)
    _badge(fig, cls, x=0.775, y=0.915, w=0.205, h=0.07)
    # right top: lead per storm
    ax3 = fig.add_axes([0.70, 0.52, 0.28, 0.30])
    _style(ax3)
    rows = [r for r in storm_rows if r["lead_min"] is not None]
    ylab = [f"{r['station']} {r['date'][:4]} (class {r['hazard_class']})" for r in rows]
    ys = range(len(rows))
    h = 0.26
    for off, key, col in ((-h, "lead_min", AMBER), (0, "lead_min_persistence_30", GREEN), (h, "lead_min_oracle_30", ACCENT)):
        ax3.barh([y + off for y in ys], [r[key] for r in rows], height=h * 0.9, color=col)
    ax3.axvline(0, color=TEXT, lw=1.2)
    ax3.set_yticks(list(ys))
    ax3.set_yticklabels(ylab, fontsize=10.5, color=TEXT)
    ax3.invert_yaxis()
    ax3.set_xlabel("minutes of warning before simulated water (negative = late)", color=MUTED, fontsize=10.5)
    fig.text(0.60, 0.885, "Lead time per storm", color=TEXT, fontsize=13, fontweight="bold")
    ax3.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for c in (AMBER, GREEN, ACCENT)],
               labels=["observed rain only", "30-min extrapolation", "perfect 30-min nowcast (bound)"], facecolor=BG,
               edgecolor=GRID, labelcolor=TEXT, fontsize=9.5, loc="lower center", bbox_to_anchor=(0.45, 1.0), ncol=1)
    ax4 = fig.add_axes([0.70, 0.10, 0.28, 0.28])
    _style(ax4)
    names = [f"{r['station']} c{r['hazard_class']}" for r in season_rows]
    ax4.bar(range(len(season_rows)), [r["pre_alerts_per_season"] for r in season_rows], color=GREEN)
    ax4.set_xticks(range(len(season_rows)))
    ax4.set_xticklabels(names, rotation=0, fontsize=10, color=TEXT)
    ax4.set_ylabel("pre-alerts per season", color=MUTED, fontsize=11)
    fig.text(0.60, 0.40, "Pre-alerts per Apr-Oct season, 2020-2026 (30-min extrapolation)", color=TEXT, fontsize=12, fontweight="bold")
    fig.savefig(path, dpi=100, facecolor=BG)
    plt.close(fig)
