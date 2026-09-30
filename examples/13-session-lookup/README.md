# Session 回查：叙述不等于执行结果

本练习用真实 Hermes `AIAgent` 和 GLM-5.2 完成 2 轮对话，把工具调用与回执写进 `state.db`，再关闭连接、重开数据库，通过 `session_search` 找回同一批记录。

模型说“已经记下”或“已经读过”，都需要回到工具回执核对；回执能证明本次工具观察到了什么，并不能替代系统当前状态。

## 运行

在仓库根目录执行。先按根目录说明准备依赖，并在当前终端设置 `GLM_API_KEY`；也兼容 `BIGMODEL_API_KEY`。凭证只从环境变量读取。

```bash
bash scripts/setup_deps.sh
bash examples/13-session-lookup/run.sh
```

依赖获取和安装使用准备脚本配置的代理；本练习的 `run.sh` 会清除代理环境变量，直连智谱模型接口。

## 观察过程

1. 第 1 轮请模型使用 `memory` 工具，把 Orion 项目使用 PostgreSQL、主库位于东京机房的事实写入 `MEMORY.md`。核对工具是否返回写入成功，再看模型的回复。
2. 第 2 轮延续第 1 轮上下文，请模型读取 Hermes 项目 `README.md` 开头 20 行，返回第 1 个 Markdown 一级标题。文件开头可能是 HTML，因此不要把首行当作标题。
3. 从 `state.db` 读回消息，按 `tool_call_id` 将每条工具回执对应到此前的调用。脚本检查 2 轮用户输入均已落库、工具请求均有回执，以及本次 Memory 写入和文件读取均已发生。
4. 关闭 Agent 和数据库连接，再新建 `SessionDB`。对比原始消息与重开后的内容，确认它们来自持久化记录。
5. 直接调用 `session_search(query="Orion", db=reopened)`，取得命中的 `session_id` 和 `match_message_id`；再以 `around_message_id` 展开原始消息，核对内容及工具配对。

工具调用次数和消息条数以实际运行结果为准。模型可读 1 次，也可分几次读取；脚本不预设条数。每次运行会创建 `.hermes-home/<运行时间>/`，其中保留独立 `state.db` 和 `memories/MEMORY.md`，既避免旧记忆影响本轮观察，也能在运行结束后继续回查。运行摘要保存在 `output/result.json`，日志保存在 `output/run.log`。

## 关键接口与边界

- `SessionDB()` 默认使用 `$HERMES_HOME/state.db`。
- `get_messages(session_id)` 默认只返回当前有效消息；本练习使用 `include_inactive=True` 核对已存记录。历史回退或压缩之后，不应把默认返回值称作全部历史。
- `session_search` 的查询结果帮助定位会话；`session_id` 与 `around_message_id` 用于展开消息窗口。窗口也有边界，不能据此断言系统里从未发生过某件事。
- `tool_calls[].id` 与工具消息的 `tool_call_id` 是请求和回执的配对依据；不要靠相邻位置或模型的复述配对。

本练习对应最终系统中的在线执行、Session 持久化、Memory 写入和历史回查。这里的 Memory 是当前请求触发的事实保存；后台学习、技能更新和跨实例共享不在本次验证范围内。

## 搜索与继续展开

脚本先展示每个命中的 `session_id`、`match_message_id` 和会话时间，再选择 `hit`。首次用与本次任务不相关的 `rollback` 搜索，随后改用 `Orion` 或 `PostgreSQL`，并依据 `get_session` 返回的 `started_at` 筛选本次运行时间。当前 `session_search` 没有独立的日期范围参数，不向它传不存在的参数。

为观察窗口不足的情况，脚本使用 `window=2`。若返回值显示后面还有消息，就以当前窗口末条消息的编号继续展开，去重后再配对。完整请求与结果可能跨窗口，不能在第一个小窗口中直接宣布缺少回执。窗口轨迹保存在 `output/result.json` 的 `windows` 字段中。

编号配对确认请求归属；“已受理”不等于完成，完成也不等于业务结果达到预期。没有搜到回执不能证明操作没有发生，还应检查会话、关键词、时间、展开范围和其他系统记录。Memory 保存提炼结果；Session 保存本 Harness 已记录的消息、请求及工具返回，不保证覆盖每次模型调用的全部输入或会话外动作。
