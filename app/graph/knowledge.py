"""A 出口：知识问答子图。

```
retrieve ──► gate ──┬─ strong ──► generate
                    └─ weak   ──► weak_fallback（话术 + 落池）
```

**子图与主图共享 `ConversationState`**：读 `resolved_query`，写
`evidence / answer / citation_ids`，主图照常从 state 取用。

三件事值得单独说：

1. **编号即下标**：`evidence` 的顺序就是 `[1] [2] …` 的顺序，引用解析靠这个对应。
2. **引用解析要宽容**：模型会写 `[1][2]`、`[1，2]`、甚至引用不存在的 `[5]`。
   解析不到就给空列表，**不能让引用失败导致整轮失败**。
3. **弱证据不等于不回答**：走兜底话术，同时落池——那是飞轮的入口（K5）。
"""
import re
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core import llm
from app.core.fallback import fallback_text, record_low_conf
from app.core.gate import is_strong
from app.core.prompts import kb_answer_messages
from app.core.retrieval import search
from app.graph.state import ConversationState

TOP_K = 5
TOPIC_MAX_CHARS = 30

# `[1]` / `[1][2]` / `[ 1 ]`；不匹配 `[1，2]` 这种写法——真出现就落到"解析为空"，
# 由测试兜住，不在正则里猜作者意图
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


def parse_citations(answer: str, evidence: list[dict]) -> list[str]:
    """从回答里提取 `[n]`，映射回 chunk id。

    - 去重、保序（同一条资料引用两次只留一个 id）
    - **越界编号直接丢弃**：模型可能引用 `[7]` 而证据只有 5 条
    - 解析不到返回 `[]`，不抛异常
    """
    ids: list[str] = []
    for match in _CITATION_RE.finditer(answer):
        index = int(match.group(1))
        if not 1 <= index <= len(evidence):
            continue
        chunk_id = evidence[index - 1]["id"]
        if chunk_id not in ids:
            ids.append(chunk_id)
    return ids


def _topic(state: ConversationState) -> str:
    """兜底话术里复述用户原话，**不自行概括**——概括错了会让用户觉得理解偏了。"""
    text = state.get("resolved_query") or state.get("raw_query") or ""
    return text[:TOPIC_MAX_CHARS] or "您的问题"


# ── 节点 ────────────────────────────────────────────────────


async def retrieve(state: ConversationState) -> dict:
    """检索 top-k，写入 `evidence`。空结果也照常写入（让闸去判，不在这一步短路）。"""
    query = state.get("resolved_query") or state.get("raw_query", "")
    hits = await search(query, top_k=TOP_K)
    return {"evidence": hits}


async def generate(state: ConversationState) -> dict:
    """基于证据生成回答，并解析引用。"""
    query = state.get("resolved_query") or state.get("raw_query", "")
    evidence = state.get("evidence") or []

    answer = await llm.chat(kb_answer_messages(query, evidence))
    return {
        "answer": answer,
        "citation_ids": parse_citations(answer, evidence),
        "is_placeholder": False,
    }


async def weak_fallback(state: ConversationState) -> dict:
    """证据不足：如实说没找到依据，并落池。

    落池的 `retrieved` 保留当时的命中（可能为空列表）——那是"为什么判它弱"的证据，
    飞轮审核时能区分"语料缺"与"召回差"。
    """
    query = state.get("resolved_query") or state.get("raw_query", "")
    evidence = state.get("evidence") or []
    top1_title = evidence[0].get("section") if evidence else None

    text, _next_step = fallback_text(_topic(state), top1_title)
    await record_low_conf(
        question=query,
        reason="retrieval_low_conf",
        hits=evidence,
        conversation_id=state.get("conversation_id"),
    )
    return {
        "answer": text,
        "citation_ids": [],
        "actions": [{"type": "transfer_human"}],
        "is_placeholder": False,
    }


# ── 条件边与子图 ────────────────────────────────────────────


def _gate_edge(state: ConversationState) -> str:
    """证据闸的条件边。只返回键，不写 state。"""
    return "strong" if is_strong(state.get("evidence") or []) else "weak"


def build_knowledge_graph() -> CompiledStateGraph:
    """编译知识出口子图。主图把它挂在 `knowledge` 节点上（节点名不变）。"""
    graph: Any = StateGraph(ConversationState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("weak_fallback", weak_fallback)

    graph.set_entry_point("retrieve")
    graph.add_conditional_edges(
        "retrieve", _gate_edge, {"strong": "generate", "weak": "weak_fallback"}
    )
    graph.add_edge("generate", END)
    graph.add_edge("weak_fallback", END)
    return graph.compile()
