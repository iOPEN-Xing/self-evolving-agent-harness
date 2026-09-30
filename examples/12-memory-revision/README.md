# 第 12 讲：记忆修订（错误记忆比没有更糟，修订要有依据、可追溯）

本目录用真实 Hermes `AIAgent` + glm-5.2 验证前台在明确纠正和修订要求下调用 `replace`。Memory 工具也能供后台复盘使用，但本练习同时关闭 Memory 与 Skill 自动检查，不验证后台自主发现、复盘触发或支付日志迁移。

## 你会观察到什么

1. 先用 `MemoryStore.add` 往 `MEMORY.md` 写入一条记忆，内容是"支付服务的缓存用 Redis 6.2"。
2. 新一轮对话里指出这条记忆过时，模型调用 `memory(action=replace, old_text='Redis 6.2', content='...Redis 7.2...')` 把它改对。
3. 打印修订前后 `MEMORY.md` 的全文，能明确看到那一条从 6.2 变成 7.2，其它条目没动。

## 为什么 replace 是"可追溯"的

`tools/memory_tool.py` 里 `replace(target, old_text, new_content)` 的语义：

- 用 `old_text` 当子串，在现有条目里找**唯一**匹配的那一条；
- 找不到，返回 `No entry matched ...`，让模型拿 `current_entries` 重试；
- 命中内容不同的多条，返回 `Multiple entries matched ... Be more specific`，让模型把 `old_text` 写得更具体；
- 唯一命中后整条替换、原子写盘；若匹配到完全相同的重复条目，当前实现处理第一条。

也就是说，修订不是"凭印象改一整段"，而是定位到一条、改那一条；改之前会告诉你当前有哪些条目，改之后能 diff 出到底变了哪一句。改错条目的风险被这条唯一匹配卡住了。

记忆条目用 `§`（section sign）分隔，所以 `MEMORY.md` 是一条一条可枚举的，这也是"可追溯"的物理基础。

## 运行

```bash
GLM_API_KEY=你的智谱key bash run.sh
```

一次实跑的关键结果：

| 步骤 | 结果 |
|---|---|
| 写入 | `store.add -> True`，MEMORY.md 出现"…Redis 6.2…" |
| 修订 | 模型 1 次 `memory(action=replace, old_text='Redis 6.2')` |
| 修订后 | 条目变为"…Redis 7.2，部署在 cache-01 上。" |
| 追溯 | 前后条目列表：`[...6.2...]` -> `[...7.2...]`，只动了版本号 |

## 范围与状态

教学用独立 `HERMES_HOME=.hermes-home/`，不改真实 `~/.hermes`。脚本从环境变量读 key，不硬编码凭证。
