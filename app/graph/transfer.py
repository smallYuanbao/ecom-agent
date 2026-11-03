"""转人工的统一下行载荷（B / C 两个出口共用）。

「转人工」不是子图节点：主图已有 `human` 出口，子图把转人工写成一条边到 `END`，
由触发它的节点先写好 `transfer_reason` / `answer` / `actions`。
"""

# 不解释内部原因，也不承诺任何数字
TRANSFER_TEXT = "这个问题我这边处理不了，已经为您转接人工客服。"
TRANSFER_ACTION = {"type": "transfer_human"}


def transfer(reason: str) -> dict:
    """转人工的返回体：话术 + 动作 + 原因（trace / 落池用）。"""
    return {
        "transfer_reason": reason,
        "answer": TRANSFER_TEXT,
        "actions": [dict(TRANSFER_ACTION)],
        "is_placeholder": False,
    }
