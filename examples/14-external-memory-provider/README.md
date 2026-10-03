# 第 14 章：将 Memory 接到可换后端，再检验共享

[上一节](../13-session-lookup/README.md) · [下一节](../15-skill-autocreation/README.md)

## 问题与案例

内置文件够用时无需外置；需要跨进程共享、命名空间或权限时，Provider 将召回和同步从主流程分离。文件实验保存 wiki.internal/api，新 Agent 不继承旧对话，靠 initialize / prefetch 召回；故障轮 sync_turn 抛错，前台正常而文件未更新。补充 SQLite 实验让 A 写、B 不重启召回，再让 B 写反馈、A 读到，检查双向共享与来源。

## 代码阅读路线

[run_provider.py](run_provider.py) 的 `FileBackedMemory` 实现文件后端与完成事件，`main` 观察初始化、召回、同步和故障。[run_shared_sqlite.py](run_shared_sqlite.py) 的 `SharedSQLiteMemory` 每次召回新建连接查询共享库，`Client` 管理独立本地 SessionDB / Agent。上游契约在 agent/memory_provider.py 与 agent/memory_manager.py。

[统一环境与模型准备](../../docs/MODEL_SETUP.md)：Python 3.12、deepseek-flash、官网直连。

本目录是《自进化 Agent 实战》第 14 讲的随讲实验。用真实 Hermes `AIAgent` + deepseek-flash，自己写一个 `MemoryProvider` 子类（文件后端），注册进 `MemoryManager`，看清它的生命周期、和内置 `MEMORY.md` 的边界、故障隔离，以及写操作失败时的漏账。

## 你会观察到什么

1. **生命周期**：`initialize()` 在会话启动跑一次；每轮开始 `prefetch(query)` 召回；每轮结束 `sync_turn()` 落盘。
2. **边界**：外置 provider 把事实写在自己的 `external_memory.json` 里，内置 `MEMORY.md` 一个字节都不动。
3. **召回**：首轮写入后关闭 agent，再用同一份外部文件创建新 agent，且不传入旧对话历史。`initialize` 读回事实，`prefetch` 命中，模型答出此前保存的地址。
4. **只允许一个 external provider**：再注册第二个，`MemoryManager.add_provider` 直接拒绝。
5. **漏账**：让 `sync_turn` 主动抛错，前台这一轮正常返回（interrupted=False），但外部文件逐字不变。这一轮的经历没有存进去，不能拿模型的口头答复当作写入成功。

## 关键 API

源码在 `agent/memory_provider.py`（ABC）和 `agent/memory_manager.py`：

- `MemoryProvider`：子类实现 `name`、`is_available()`、`initialize()`、`get_tool_schemas()`；按需覆写 `prefetch()` / `sync_turn()`。
- `MemoryManager().add_provider(p)`：builtin 永远放行；external 只允许一个，第二个打 warning 拒绝。
- `prefetch_all()` / `sync_all()`：管理器捕获 provider 的异常。`sync_all()` 把写入交给后台线程，前台正常返回不代表后台已经写入；练习等待同步完成事件后才检查文件。

## 运行

```bash
bash examples/14-external-memory-provider/run.sh
```

脚本会自动核对以下结果，任一项不满足便以非零状态退出：

```
已注册外部 provider: ['filemem']
再注册第二个后: ['filemem']            # 第二个 external 被拒
步骤1  [filemem] sync_turn()  已存，现共 1 条
       [内置 MEMORY.md] 逐字未变      # 对照运行前的文件内容
步骤2  [filemem] prefetch()  命中=1 条  -> 模型答出 wiki.internal/api
步骤3  [filemem] sync_turn() 抛 RuntimeError
       [前台] text_response(...)  interrupted=False
       外部记忆文件条数 2 -> 2         # 这一轮经历没存进去，漏账
```

## 写入失败意味着什么

记忆可以外置、可移植、可换成别的后端，但前台正常返回和后台写入成功是两回事。本练习实际注入写入故障，证明失败后会漏记这一轮经历；读取故障需要另外验证。旧手册也不能代替现场查询，外置和内置两份存储不一致时，应回当前环境核实。

## 范围与状态

每次 `run.sh` 都在 `output/run-XXXXXXXX/` 下创建独立且保留的 `hermes-home/`，不改真实 `~/.hermes`。`output/latest-run.txt` 记录本次运行目录，其中保存 `run.log`、`run-summary.json` 和外部记忆文件。输出目录不加入版本管理。

脚本只从 `DEEPSEEK_API_KEY` 读取凭证，运行前清除大小写代理变量。可用 `HERMES_SRC` 指定依赖目录，默认使用仓库内的 `.deps/hermes-agent`；使用该目录的 `.venv/bin/python`。

为单独观察外部 provider，模型轮次不开放工具。内置 `MEMORY.md` 预先写入一条无关约定，脚本在每轮后逐字比较它与原文件；这证明本次外部 provider 没有改写内置文件，不表示所有启用内置记忆工具的对话都不会修改该文件。召回轮重建 agent 且不传旧对话历史，用来排除模型从旧对话中取得地址的可能。

## 最小双向共享实验

保留上面的文件后端实验，另运行：

```bash
.deps/hermes-agent/.venv/bin/python examples/14-external-memory-provider/run_shared_sqlite.py
# 可选：用两个真实 AIAgent 执行模型轮次；需要模型凭证。
.deps/hermes-agent/.venv/bin/python examples/14-external-memory-provider/run_shared_sqlite.py --with-model
```

默认模式使用真实 Hermes `MemoryManager` 和两个独立 `SessionDB`，直接检查 Provider 读写，不调用模型。A、B 本地目录与会话编号独立，连接同一 `shared.sqlite3` 的 `shared-lab` 范围，代理标识分别为 A、B。B 先启动并召回空结果，A 写入后 B 不重启即可召回；B 保存不同条件下的反馈后，A 能读到两份记录。

每次召回建立连接重新查询 SQLite，不使用启动时读一次的事实缓存。写入用 `BEGIN IMMEDIATE` 与提交事务；后台完成事件、写入错误和管理器完成状态分开检查。结果保存在新建的 `output/shared-*/shared-summary.json`，保留来源代理、会话编号、条件与结果。

`--with-model` 使用两个真实 `AIAgent`，各自使用独立本地目录和数据库，仍通过同一个自建 SQLite Provider 读写。这一模式需要单独运行验证；默认存储检查通过不证明模型路径或业务结果通过。

案例是构造的教学数据，`business_effect_verified`、`joint_evolution_verified` 均保持 `false`。这不实现 OpenViking 的经验提炼或分层检索，也不实现生产鉴权。共享地址只是起点；身份、命名空间、权限和检索范围需要由后端明确配置。读到 A 的案例后，还需核查来源、适用条件、当前验证、反馈写回与后续正确采用。

## 共享存储与共同学习之间的距离

Provider 的 initialize 建立后端状态，prefetch 提供当前召回，sync_turn 保存本轮材料。文件后端把用户文本原样追加，并用简单关键词匹配召回；它没有自动核验事实、提炼方法或解决矛盾。因此可替换的接口解决接线问题，知识质量仍要另管。

SQLite 补充实验每次召回查询同一共享库，避免把实例启动时的旧内存缓存误当实时共享。B 读到 A、A 读到 B 说明记录可互见；要形成共同学习，还需核验反馈条件、生成候选、独立评测并让下一任务实际采用。

## 在学习循环中的作用

Provider 将事实召回与同步接到后端，双向共享补充实验检查其它实例是否能读到新记录。共享事实是方法学习的输入之一；进入 Skill 更新之前，还需明确来源、条件和哪些结论已经过现场验证。

## 工程应用与观察练习

共享后端明确身份、租户、范围、权限与来源；写入以事务提交和完成事件验证。SQLite BEGIN IMMEDIATE 处理本练习事务竞争，不等于跨地域高可用。召回旧事实仍要检查更新时间与现场状态。本例未实现 OpenViking 的分层检索，构造反馈读回也不证明业务收益或共同进化。

在 sync_turn 故障轮分别核对前台结果、完成事件、异常和文件内容。再从 SQLite 共享记录中追到来源代理与会话，说明读回反馈与验证反馈正确之间还差哪一步。
