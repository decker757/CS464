"""15 — The LMSR pricing engine, for a two-outcome market.

Two panels, side by side, for three values of the liquidity parameter b:

  left   the price of YES as a trader buys YES shares from q = (0, 0)
  right  what buying x YES shares from q = (0, 0) costs, beside the naive
         x × 0.50 a trader would pay if the opening price never moved, and
         the x × 1.00 that YES pays out if it wins; the gap between that line
         and a cost curve is the market maker's loss, and it never exceeds b·ln 2

Every price and cost on the curves is computed by the repo's own engine,
`core/lmsr.py`, called the way the ledger calls it: `Decimal` in, `Decimal`
out, inside the engine's own 50-digit context. The script computes only the
two straight reference lines (x × 0.50 and x × 1.00), and the b·ln 2 it
prints is the engine's own `cost` at q = (0, 0), not a formula of its own.

Run from the repository root:

    python docs/diagrams/15-lmsr.py

It needs matplotlib and nothing else: no environment variables, no database.
It writes `rendered/15-lmsr.svg` and `rendered/15-lmsr.png` (1600 px wide,
white background) beside this file.

Drawn from:
  backend/ledger_service/core/lmsr.py — `prices` and `cost_to_trade`
  backend/ledger_service/core/pricing.py — `trade_cost_of`, the engine's caller
  backend/market_service/core/config.py — `default_liquidity_b`, 100, the b a
    market gets when the administrator gives none (DEFAULT_LIQUIDITY_B)
  backend/market_service/core/opening_prices.py — `max_platform_loss`, b·ln(n)
  ADR 0005 — the engine is a module on the ledger, not a service
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (after the backend is chosen)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUTPUT = HERE / "rendered"

# The ledger imports the engine as `core.lmsr` with `backend/ledger_service`
# on the path, and so does this. `core/__init__.py` is empty and `lmsr.py`
# imports only the standard library, so the import reads no settings and
# needs no environment.
sys.path.insert(0, str(REPO / "backend" / "ledger_service"))
from core import lmsr  # noqa: E402

# The three markets drawn. 100 is the real default: `default_liquidity_b` in
# backend/market_service/core/config.py, which `_apply` in
# market_service/service/market_service.py gives any market saved without a b,
# and which every e2e market uses. The schema bounds b only by > 0 and
# `MAX_PRICING_VALUE`, so 50 and 200 are a halving and a doubling of the
# default, not values the code names.
DEFAULT_B = 100
B_VALUES = [50, DEFAULT_B, 200]

# Shares of YES bought, 0 to 400 in steps of 2.
SHARES = list(range(0, 401, 2))

# One hue, darker as b grows, and a different dash for each b, so the three
# lines still separate in greyscale. References are grey and dotted.
LINE_STYLE = {
    50: {"color": "#3987e5", "linestyle": (0, (5, 2))},
    100: {"color": "#1c5cab", "linestyle": "-"},
    200: {"color": "#0d366b", "linestyle": (0, (6, 2, 1.5, 2))},
}
REFERENCE_STYLE = {"color": "#7a7a7a", "linestyle": ":", "linewidth": 1.1}
TEXT_COLOUR = "#1A202C"
GRID_COLOUR = "#e4e4e4"

# Drawn 17 cm wide. Placed at the 15.9 cm text width, 7 pt text prints at about 6.5 pt.
FIGURE_WIDTH_IN = 6.69
FIGURE_HEIGHT_IN = 2.65
PNG_WIDTH_PX = 1600


def yes_price_after_buying(shares: int, b: int) -> float:
    """The marginal price of YES at q = (shares, 0)."""
    q = [Decimal(shares), Decimal(0)]
    yes_price, _no_price = lmsr.prices(q, Decimal(b))
    return float(yes_price)


def cost_of_buying_yes(shares: int, b: int) -> float:
    """What buying `shares` of YES from q = (0, 0) costs, in credits."""
    q = [Decimal(0), Decimal(0)]
    delta = [Decimal(shares), Decimal(0)]
    return float(lmsr.cost_to_trade(q, Decimal(b), delta))


def label_for(b: int) -> str:
    if b == DEFAULT_B:
        return f"b = {b} (default)"
    return f"b = {b}"


def style_axes(axes) -> None:
    axes.grid(True, color=GRID_COLOUR, linewidth=0.6)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color("#8a8a8a")
    axes.tick_params(colors=TEXT_COLOUR, labelsize=7)
    axes.set_xlim(0, SHARES[-1])


def draw_price_panel(axes) -> None:
    for b in B_VALUES:
        price_line = [yes_price_after_buying(shares, b) for shares in SHARES]
        axes.plot(SHARES, price_line, linewidth=1.5, label=label_for(b), **LINE_STYLE[b])

    axes.axhline(0.5, **REFERENCE_STYLE)
    axes.text(SHARES[-1], 0.48, "opening price 0.50", ha="right", va="top",
              fontsize=7, color="#555555")

    axes.set_ylim(0.45, 1.0)
    axes.set_title("Price of YES as YES is bought", fontsize=8.5, color=TEXT_COLOUR)
    axes.set_xlabel("YES shares bought from q = (0, 0)", fontsize=7.5, color=TEXT_COLOUR)
    axes.set_ylabel("Price of YES (credits per share)", fontsize=7.5, color=TEXT_COLOUR)
    axes.legend(loc="lower right", bbox_to_anchor=(1.0, 0.1), fontsize=7, frameon=False)
    style_axes(axes)


def draw_cost_panel(axes) -> None:
    for b in B_VALUES:
        cost_line = [cost_of_buying_yes(shares, b) for shares in SHARES]
        axes.plot(SHARES, cost_line, linewidth=1.5, label=label_for(b), **LINE_STYLE[b])

    payout_line = [shares * 1.0 for shares in SHARES]
    naive_line = [shares * 0.5 for shares in SHARES]
    axes.plot(SHARES, payout_line, **REFERENCE_STYLE)
    axes.plot(SHARES, naive_line, **REFERENCE_STYLE)
    # Rotated in data coordinates, so each label lies along its line.
    axes.text(186, 214, "x × 1.00: paid out if YES wins", rotation=45,
              transform_rotates_text=True, rotation_mode="anchor",
              ha="center", va="bottom", fontsize=7, color="#555555")
    axes.text(300, 142, "x × 0.50: no price impact", rotation=26.57,
              transform_rotates_text=True, rotation_mode="anchor",
              ha="center", va="top", fontsize=7, color="#555555")

    # The market maker's loss if YES wins at the default b: the payout minus
    # what the buyer paid. It approaches b·ln 2 and never reaches it. The
    # engine's own cost at q = (0, 0) is that bound, b·ln(n) with n = 2.
    worst_case = float(lmsr.cost([Decimal(0), Decimal(0)], Decimal(DEFAULT_B)))
    at_shares = SHARES[-1] - 6
    paid = cost_of_buying_yes(at_shares, DEFAULT_B)
    axes.annotate("", xy=(at_shares, at_shares), xytext=(at_shares, paid),
                  arrowprops={"arrowstyle": "<->", "color": TEXT_COLOUR,
                              "linewidth": 0.8, "shrinkA": 0, "shrinkB": 0})
    axes.annotate(f"maker's loss at b = {DEFAULT_B}:\nunder b·ln 2 = {worst_case:.1f}",
                  xy=(at_shares - 1, at_shares - 12), xytext=(318, 392),
                  ha="right", va="top", fontsize=7, color=TEXT_COLOUR,
                  arrowprops={"arrowstyle": "-", "color": TEXT_COLOUR,
                              "linewidth": 0.6, "shrinkA": 2, "shrinkB": 0})

    axes.set_ylim(0, SHARES[-1])
    axes.set_title("Cost of buying x YES shares", fontsize=8.5, color=TEXT_COLOUR)
    axes.set_xlabel("x, YES shares bought from q = (0, 0)", fontsize=7.5, color=TEXT_COLOUR)
    axes.set_ylabel("Cost of the trade (credits)", fontsize=7.5, color=TEXT_COLOUR)
    axes.legend(loc="upper left", fontsize=7, frameon=False)
    style_axes(axes)


def main() -> None:
    figure, (price_axes, cost_axes) = plt.subplots(
        1, 2, figsize=(FIGURE_WIDTH_IN, FIGURE_HEIGHT_IN), layout="constrained"
    )
    figure.patch.set_facecolor("white")
    draw_price_panel(price_axes)
    draw_cost_panel(cost_axes)

    OUTPUT.mkdir(exist_ok=True)
    figure.savefig(OUTPUT / "15-lmsr.svg", facecolor="white")
    figure.savefig(OUTPUT / "15-lmsr.png", facecolor="white",
                   dpi=PNG_WIDTH_PX / FIGURE_WIDTH_IN)
    print(f"wrote {OUTPUT / '15-lmsr.svg'} and {OUTPUT / '15-lmsr.png'}")


if __name__ == "__main__":
    main()
