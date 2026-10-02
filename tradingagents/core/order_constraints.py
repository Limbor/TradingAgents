"""Deterministic exchange-order constraints.

LLMs may recommend percentages, but executable share quantities must be
validated by code before they reach a portfolio plan or holding mutation.
"""

from __future__ import annotations

import math

CN_A_BOARD_LOT = 100


def is_cn_a_symbol(symbol: str) -> bool:
    normalized = str(symbol or "").strip().upper()
    return normalized.endswith((".SH", ".SZ", ".BJ"))


def _whole_shares(value: float) -> int | None:
    numeric = float(value)
    rounded = round(numeric)
    if not math.isfinite(numeric) or not math.isclose(numeric, rounded, abs_tol=1e-9):
        return None
    return int(rounded)


def validate_cn_a_sell_quantity(
    current_quantity: float,
    sell_quantity: float,
) -> tuple[bool, str]:
    """Validate an A-share auction sell quantity against the current holding.

    A whole-lot holding must be sold in 100-share lots.  When a holding has an
    odd-lot remainder, that remainder may be sold once on its own or together
    with whole lots; it may not be split into smaller odd lots.  A full exit is
    always valid, including for an odd-lot-only holding.
    """

    current = _whole_shares(current_quantity)
    sell = _whole_shares(sell_quantity)
    if current is None or current <= 0:
        return False, "当前 A 股持仓数量必须是正整数"
    if sell is None or sell <= 0:
        return False, "A 股卖出数量必须是正整数股"
    if sell > current:
        return False, f"卖出数量 {sell} 股超过当前持仓 {current} 股"
    if sell == current:
        return True, ""

    remainder = current % CN_A_BOARD_LOT
    if remainder == 0:
        valid = sell % CN_A_BOARD_LOT == 0
    else:
        valid = sell % CN_A_BOARD_LOT in {0, remainder}
    if valid:
        return True, ""

    if current <= CN_A_BOARD_LOT:
        return (
            False,
            f"当前仅持有 {current} 股，不支持部分减仓；如需卖出必须一次性清仓 {current} 股",
        )
    odd_lot = f"；零股余数 {remainder} 股只能一次性卖出" if remainder else ""
    return (
        False,
        f"A 股部分卖出数量须为 {CN_A_BOARD_LOT} 股的整数倍{odd_lot}",
    )


def cn_a_partial_sell_available(current_quantity: float) -> bool:
    """Return whether at least one legal sell can leave a non-zero holding."""

    current = _whole_shares(current_quantity)
    return current is not None and current > CN_A_BOARD_LOT
