"""知识库加载与切分。

**切分规则只有一条：按 `## ` 二级标题切。** 平台文档和法规共用同一套代码——
法规文件里每个 `## 第六条 …` 正好就是一条，所以不需要第二套解析器。

chunk id 有两种形态：

| 来源 | id 形态 | 例子 |
|---|---|---|
| 平台文档 | `{文件名}#{小节名}` | `returns#退货运费由谁承担` |
| 法规 | `art-{条号:03d}` | `art-006` |

**为什么把标题拼进 text**（三条作用，强弱不同）：

1. **区分语义角色**（最重要）：`本文档不提供什么` 那一行是给模型的**指令**；没有标题，
   「不计算退款金额」会被当成一句政策内容读，而不是"你别做这件事"。
2. **引用自解释**：citation 片段带标题，用户点开就知道这段是政策正文还是边界声明。
3. **短小节的检索信号**：`shipping#运费险` 正文仅 127 字，标题词对 BM25 是额外加权。
   长小节里这个作用会被正文稀释——**不要夸大它**。
"""
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "kb"
REGULATION_DIR = DATA_DIR / "regulations"

# `## 第六条 不适用七日无理由退货的商品`
_ARTICLE_RE = re.compile(r"^第([一二三四五六七八九十]+)条\s*(.*)$")
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str) -> int:
    """把「六」「十二」「二十」这类条号转成整数。

    只支持 1–99：法规条号不会更大，超出范围直接报错比静默算错好。
    """
    if not text:
        raise ValueError("空条号")
    if text == "十":
        return 10
    if "十" in text:
        tens_text, _, ones_text = text.partition("十")
        tens = _CN_DIGITS[tens_text] if tens_text else 1
        ones = _CN_DIGITS.get(ones_text, 0) if ones_text else 0
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_DIGITS:
        return _CN_DIGITS[text]
    raise ValueError(f"不支持的条号写法：{text}")


def split_sections(markdown: str) -> list[tuple[str, str]]:
    """按 `## ` 切分，返回 `[(小节标题, 正文)]`。

    `#` 一级标题是文档名，不进 chunk（它作为 metadata 的 source 保留）。
    小节正文会被 strip；空小节省略。
    """
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    buffer: list[str] = []

    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_title is not None:
                body = "\n".join(buffer).strip()
                if body:
                    sections.append((current_title, body))
            current_title = line[3:].strip()
            buffer = []
        elif current_title is not None:
            buffer.append(line)

    if current_title is not None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append((current_title, body))

    return sections


def _doc_chunks(path: Path) -> list[dict]:
    chunks: list[dict] = []
    for title, body in split_sections(path.read_text(encoding="utf-8")):
        chunks.append(
            {
                "id": f"{path.stem}#{title}",
                "text": f"{title}\n{body}",
                "source": path.name,
                "section": title,
                "kind": "doc",
            }
        )
    return chunks


def _regulation_chunks(path: Path) -> list[dict]:
    chunks: list[dict] = []
    for title, body in split_sections(path.read_text(encoding="utf-8")):
        match = _ARTICLE_RE.match(title)
        if match is None:
            # 只收录到条；文件头的说明段（没有 `## `）本来就不会进这里
            raise ValueError(f"{path.name} 里有非条号小节：{title!r}")
        article_no = cn_to_int(match.group(1))
        chunks.append(
            {
                "id": f"art-{article_no:03d}",
                "text": f"{title}\n{body}",
                "source": path.name,
                "section": title,
                "kind": "regulation",
                "article_no": article_no,
            }
        )
    return chunks


def load_chunks() -> list[dict]:
    """读取全部语料并切分。按 id 排序，保证每次运行顺序一致（哈希才稳定）。"""
    chunks: list[dict] = []
    for path in sorted(DATA_DIR.glob("*.md")):
        chunks.extend(_doc_chunks(path))
    for path in sorted(REGULATION_DIR.glob("*.md")):
        chunks.extend(_regulation_chunks(path))
    return sorted(chunks, key=lambda c: c["id"])
