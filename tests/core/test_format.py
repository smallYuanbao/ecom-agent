"""金额格式：分 → 人话。

B / C 两个出口共用一份，避免出现「1249.00 元」和「1249 元」两种口径。
"""
import pytest

from app.core.format import yuan_text


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "0.00 元"),
        (1, "0.01 元"),
        (99, "0.99 元"),
        (100, "1.00 元"),
        (124900, "1249.00 元"),
        (269900, "2699.00 元"),
        (123456, "1234.56 元"),
    ],
)
def test_yuan_text(cents: int, expected: str):
    assert yuan_text(cents) == expected


def test_large_value_stays_exact():
    """浮点除法在 2^53 附近会丢精度；整数运算不会。"""
    assert yuan_text(2**53 - 1) == "90071992547409.91 元"
