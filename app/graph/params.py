"""从用户原话里抽结构化参数。

**只做字面提取，不做业务校验**：订单号存不存在、是不是本人的、日期对不对，
分别由 `validate` / 工具层负责。正则混进业务判断，会让两处都难测。
"""
import re

# 订单号：4 位以上连续数字，左右不能再接数字。
# `(?<!\d)` / `(?!\d)` 防的是从 `123456` 里抠出前 4 位。
ORDER_ID_RE = re.compile(r"(?<!\d)(\d{4,})(?!\d)")

# SKU：两个字母 + 连字符 + 两位数字，如 `ST-20` / `FB-10`
SKU_RE = re.compile(r"[A-Za-z]{2}-\d{2}")


def extract_order_id(text: str) -> str | None:
    """从用户原话里抠订单号；抠不到返回 `None`。

    已知局限：手机号这类长数字也会被当成订单号。它会在查库时找不到，
    退回 `need_info` 列候选——**错得可控**，所以不为它加特判。
    """
    match = ORDER_ID_RE.search(text or "")
    return match.group(1) if match else None


def extract_sku(text: str) -> str | None:
    """从用户原话里抠 SKU，统一转大写；抠不到返回 `None`。

    只认 `XX-00` 这种格式。用户说「推车」而不是型号时抽不到，
    由 `need_info` 列可选型号——不猜商品名。
    """
    match = SKU_RE.search(text or "")
    return match.group(0).upper() if match else None
