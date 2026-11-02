# tests 目录说明

**按 `app/` 分层，不按章分。**

章是"什么时候写的"，测试应该按"测什么"组织。同一个测试文件会跨章被改
（例如 `test_models.py` 从 Ch01 一直改到 Ch03），按章分会让每次修改都产生搬移。

## 结构

```
tests/
├── conftest.py            # 全局夹具：临时库、LLM 客户端清理
├── db/                    # app/db/     模型、仓库、seed、session
├── core/                  # app/core/   检索、证据闸、知识库加载、评测集
├── rules/                 # app/rules/  纯函数：资格判定、金额计算
├── graph/                 # app/graph/  意图、路由、主图、各出口
│   └── rules/             # C 出口子图：9 个节点 + 集成测试
├── api/                   # app/api/    SSE 帧与 HTTP 层
├── eval/                  # gold set 的结构守卫
└── docs/                  # 文档与代码的一致性守卫
```

## 常用命令

```bash
uv run pytest                          # 全量
uv run pytest tests/graph/rules        # 只跑退款流水线
uv run pytest tests/rules              # 只跑纯函数
uv run pytest tests/db                 # 只跑数据层
uv run pytest -k refund                # 按名字过滤
uv run pytest --lf                     # 只跑上次失败的
```

## 命名约定

| 目录 | 约定 | 例子 |
|---|---|---|
| `rules/` | 测 `app/rules/<name>.py` | `test_eligibility.py` |
| `graph/rules/` | 测子图节点 `<node>` | `test_intake.py`、`test_judge.py` |
| 其他 | 与 `app/` 下的模块同名 | `test_retrieval.py` |

**集成测试**（跑整张图、打桩外部依赖）也放在对应目录，不单独建 `integration/`。
需要单独跑时用 `-k` 或 pytest marker，不靠目录区分。

## 新增测试时

- 文件放到对应层的目录，不要堆在 `tests/` 根目录
- 依赖 `data/` 或 `docs/` 时，用 `Path(__file__).resolve().parents[2]` 定位仓库根
- 外部服务（模型 / Chroma / Ollama）一律打桩；真服务测试用 `skipif` 标出来
