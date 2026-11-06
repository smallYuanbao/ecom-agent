"""S1/S4：越权与不存在走同一条路径。

三条路径都要覆盖：C 规则子图、B 数据工具、E ReAct 执行器。
另加两条静态守卫：仓库函数必须 keyword-only `user_id`；工具 schema 不能暴露 `user_id`。
"""
import inspect
from datetime import datetime

import pytest
import pytest_asyncio

from app.api.chat import ChatError, conversation_access
from app.core import react
from app.db.errors import NotFound
from app.db.models import Logistics, Order
from app.db.repo_conversation import (
    create_conversation,
    get_conversation,
    list_conversations,
)
from app.db.repo_order import get_coupons, get_order, get_tracking, list_user_orders
from app.graph import rules_exit
from app.tools.orders import query_logistics, query_order

CREATED = datetime(2026, 10, 20, 9, 0)


def _order(order_id: str, user_id: str, tracking_no: str | None = None) -> Order:
    return Order(
        id=order_id,
        user_id=user_id,
        sku_id="ST-20",
        spec="石墨灰",
        category="婴儿推车",
        status="已签收",
        amount=129900,
        quantity=1,
        created_at=CREATED,
        tracking_no=tracking_no,
    )


@pytest_asyncio.fixture
async def seeded(session):
    session.add_all([_order("1001", "u1"), _order("2001", "u2", tracking_no="SF2002001")])
    await session.flush()

    session.add(
        Logistics(
            tracking_no="SF2002001",
            order_id="2001",
            latest_status="已签收",
            nodes="[]",
            updated_at=CREATED,
        )
    )
    await session.flush()
    return session


async def test_repo_other_user_same_as_missing(session, seeded):
    with pytest.raises(NotFound) as other:
        await get_order("2001", user_id="u1", session=seeded)

    with pytest.raises(NotFound) as missing:
        await get_order("9999", user_id="u1", session=seeded)

    assert str(other.value) == str(missing.value)


async def test_rules_path_does_not_leak_other_user(seeded, monkeypatch):
    async def scoped(order_id: str, *, user_id: str):
        return await get_order(order_id, user_id=user_id, session=seeded)

    monkeypatch.setattr(rules_exit, "get_order", scoped)

    result = await rules_exit.validate(
        {
            "order_id": "2001",
            "user_id": "u1",
            "refund_draft": {"reason_code": "no_reason"},
        }
    )

    assert "order_not_found" in result["validate_errors"]
    assert "order_snapshot" not in result


async def test_data_tool_other_user_same_as_missing(seeded):
    other = await query_order("2001", user_id="u1", session=seeded)
    missing = await query_order("9999", user_id="u1", session=seeded)

    assert other.ok is missing.ok is False
    assert other.code == missing.code == "not_found"
    assert other.data is None and missing.data is None


async def test_logistics_other_user_is_not_found(seeded):
    result = await query_logistics("SF2002001", user_id="u1", session=seeded)
    assert result.ok is False
    assert result.code == "not_found"


async def test_react_executor_other_user_is_not_found(seeded, monkeypatch):
    async def scoped(order_id: str, *, user_id: str):
        return await query_order(order_id, user_id=user_id, session=seeded)

    monkeypatch.setattr(react.order_tools, "query_order", scoped)

    result = await react.execute_tool(
        "query_order", {"order_id": "2001"}, user_id="u1"
    )

    assert result["code"] == "not_found"
    assert "data" not in result


@pytest.mark.parametrize(
    "fn",
    [
        get_order,
        list_user_orders,
        get_tracking,
        get_coupons,
        get_conversation,
        list_conversations,
    ],
)
def test_user_scoped_repos_require_keyword_user_id(fn):
    param = inspect.signature(fn).parameters["user_id"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_tool_schemas_do_not_expose_user_id():
    for spec in react.TOOL_SPECS.values():
        assert "user_id" not in spec.parameters["properties"]


async def test_conversation_access_rejects_other_user(session):
    cid = await create_conversation("u2", session=session)

    with pytest.raises(ChatError) as exc:
        await conversation_access(cid, user_id="u1", session=session)

    assert exc.value.code == "not_found"


async def test_conversation_access_allows_owner_and_new(session):
    cid = await create_conversation("u1", session=session)

    await conversation_access(cid, user_id="u1", session=session)
    await conversation_access(None, user_id="u1", session=session)
