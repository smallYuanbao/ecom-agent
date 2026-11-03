"""从原话抽订单号 / SKU。

只测**字面提取**：存不存在、是不是本人的，由后续节点负责。
"""
import pytest

from app.graph.params import extract_order_id, extract_sku


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("订单 1001 我要退款", "1001"),
        ("1001", "1001"),
        ("我的单号是1002，想退", "1002"),
        ("abc1003xyz", "1003"),
        ("12345678", "12345678"),
        ("我要退款", None),
        ("123", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_order_id(text, expected):
    assert extract_order_id(text) == expected


def test_order_id_does_not_slice_a_longer_number():
    """`(?<!\\d)` / `(?!\\d)` 的作用：不能从长数字里抠出前 4 位。"""
    assert extract_order_id("123456") == "123456"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ST-20 还有货吗", "ST-20"),
        ("st-20 还有货吗", "ST-20"),
        ("FB-10 多少钱", "FB-10"),
        ("推车还有货吗", None),
        ("ST-2 有货吗", None),       # 位数不够
        ("", None),
    ],
)
def test_extract_sku(text, expected):
    assert extract_sku(text) == expected
