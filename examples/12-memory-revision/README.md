# 第 12 章：纠正错误事实，核对实际修订

[上一节](../11-background-review/README.md) · [下一节](../13-session-lookup/README.md)

## 问题与案例

前一章讨论何时复盘，本章只观察明确纠正下的 Memory 修订。事实会过期：缓存已升级时，旧版本继续进入请求可能让调查使用错误条件。起点是“Redis 6.2，部署在 cache-01”，用户要求改成 7.2并保留其余内容。replace 定位旧条目后整条替换，所以新 content 必须保留仍有效的部署主机；版本写对却丢失主机也属于失败。

## 代码阅读路线

[run_memory_revision.py](run_memory_revision.py) 的 `list_memory_operations` 提取实际操作，兼容单次与 operations 批量形式，`main` 限制工具为 memory、关闭自动复盘，并创建独立 `.hermes-home/<run_id>/`。纯校验位于 [revision_validation.py](revision_validation.py)：`revision_checks` 要求正常 Turn、配对 replace 成功回执和只替换版本号的实际文件变化。摘要保存在该运行目录的 `revision-result.json`，失败退出非零。

[统一环境与模型准备](../../docs/MODEL_SETUP.md)：Python 3.12、deepseek-flash、官网直连。

本目录用真实 Hermes `AIAgent` + deepseek-flash 验证前台在明确纠正和修订要求下调用 `replace`。Memory 工具也能供后台复盘使用，但本练习同时关闭 Memory 与 Skill 自动检查，不验证后台自主发现、复盘触发或支付日志迁移。

## 你会观察到什么

1. 先用 `MemoryStore.add` 往 `MEMORY.md` 写入一条记忆，内容是"支付服务的缓存用 Redis 6.2"。
2. 新一轮对话里指出这条记忆过时，模型通过 memory 的 replace 操作修订。固定版本支持单次与 `operations` 批量形状；只看顶层 action 会漏掉真实批量调用。
3. 打印修订前后 `MEMORY.md` 的全文，能明确看到那一条从 6.2 变成 7.2，其它条目没动。

等价的批量参数如下；`content` 是完整新条目，`old_text` 只是匹配条件：

```json
{"target": "memory", "operations": [{"action": "replace", "old_text": "Redis 6.2", "content": "支付服务的缓存用 Redis 7.2，部署在 cache-01 上。"}]}
```

## 为什么 replace 是"可追溯"的

`tools/memory_tool.py` 里 `replace(target, old_text, new_content)` 的语义：

- 用 `old_text` 当子串，在现有条目里找**唯一**匹配的那一条；
- 找不到，返回 `No entry matched ...`，让模型拿 `current_entries` 重试；
- 命中内容不同的多条，返回 `Multiple entries matched ... Be more specific`，让模型把 `old_text` 写得更具体；
- 唯一命中后整条替换、原子写盘；若匹配到完全相同的重复条目，当前实现处理第一条。

唯一匹配限制了修改对象，但没有保证替换后的内容完整。工具回执告诉我们写入是否成功，回读与 diff 才能发现主机信息丢失等问题。正常完成、成功回执与正确文件变化必须同时满足。

记忆条目用 `§`（section sign）分隔，所以 `MEMORY.md` 是一条一条可枚举的，这也是"可追溯"的物理基础。

## 运行

```bash
bash examples/12-memory-revision/run.sh
```

## 范围与状态

教学用独立 `HERMES_HOME=.hermes-home/<run_id>/`，不改真实 `~/.hermes`。脚本从环境变量读 key，不硬编码凭证。

## 在学习循环中的作用

这一节更新的是事实条目，决定修改的依据来自用户明确纠正。它验证了受控 Memory 维护；接下来还需在新的任务中观察召回，才能判断修订是否影响后续执行。可复用调查方法的生成在第 15–16 章展开。

## 工程应用与观察练习

生产修订应绑定原条目版本、来源与修改理由，回读实际结果，并防止并发覆盖。replace 成功证明文本写入，不证明升级事实真实。Memory 若没有完整审批历史，应在外层追加审计。本前台实验不验证后台自主发现或新会话采用。

把新 content 故意改成只含 Redis 7.2，保留一次成功工具回执，核对 exact_revision 为什么仍失败。再为新 Agent 设计召回任务，说明应保存哪份输入，才能判断它是否读到了修订后的主机与版本。
