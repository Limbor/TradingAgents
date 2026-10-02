from tradingagents.core.order_constraints import (
    cn_a_partial_sell_available,
    validate_cn_a_sell_quantity,
)


def test_cn_a_100_share_holding_cannot_be_reduced_by_50():
    valid, reason = validate_cn_a_sell_quantity(100, 50)

    assert valid is False
    assert "不支持部分减仓" in reason
    assert cn_a_partial_sell_available(100) is False


def test_cn_a_whole_lots_and_full_exit_are_valid():
    assert validate_cn_a_sell_quantity(300, 100) == (True, "")
    assert validate_cn_a_sell_quantity(300, 300) == (True, "")
    assert cn_a_partial_sell_available(300) is True


def test_cn_a_odd_lot_remainder_must_not_be_split():
    assert validate_cn_a_sell_quantity(250, 50) == (True, "")
    assert validate_cn_a_sell_quantity(250, 150) == (True, "")
    valid, reason = validate_cn_a_sell_quantity(250, 25)

    assert valid is False
    assert "零股余数 50 股只能一次性卖出" in reason
