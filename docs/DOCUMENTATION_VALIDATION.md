# DeepSeek 迁移验证记录（633dc89）

本页保留提交 `633dc89` 对应的真实模型与离线验证结果。后续的自进化表述和文档检查升级见 [复核记录](EVOLUTION_REVIEW.md)；后续离线通过不会改变或扩展下面的真实模型覆盖范围。

阅读本页时按“执行协议、完成状态、实际回执、业务评分、结论范围”逐项判断。正常 Turn 能证明任务结束，评分能说明当前题组的判断；可信来源、加载与独立题条件还需各自核对。当前文档整理见 [逐份复核](DOCUMENTATION_AUDIT.md)，没有复用旧结果声称新的运行成功。

本轮在 macOS、Python 3.12.14 下整理整套章节。主线 01–23 章均有对应文档，第 10 章是机制桥接；另整理 Assembly、Capstone 和快照附录。章节路径、代码入口、函数与稳定 Notebook 单元 ID 由 [chapters.json](chapters.json) 声明，`scripts/check_docs.py` 检查失效引用。

## 真实模型执行

使用 `deepseek-flash` 与 `https://api.deepseek.com`，凭证仅由环境提供。Notebook 采用官网原生 Responses；自建 Chat 循环关闭 thinking。运行源 Notebook 保持无输出，执行副本、完整请求和原始日志保留在本机私有运行目录，公共报告只保存脱敏摘要与哈希。

| 路径 | 本轮结果 | 结论范围 |
|---|---|---|
| Chat / Responses 探针 | 均 HTTP 200，短回答匹配 | 当前官网接口与模型名可用 |
| SDK 最小 Turn | 正常 completed，回答 SDK_OK | 固定 SDK 可使用原生 Responses |
| 01 Agent Loop | 默认 Notebook 无异常完成 | 实际读取教学材料并生成报告 |
| 02 steer / interrupt | 默认 Notebook 无异常完成 | 包括活跃 Turn 投递与独立中断对照 |
| 03 上下文装配 | 默认 Notebook 无异常完成 | 文件读取前后的真实请求可观察 |
| 04 项目规则 | 默认 Notebook 无异常完成 | 根 / 局部规则的请求对照完成 |
| 05 上下文压缩 | 默认 Notebook 无异常完成 | 原生压缩事件与采样追问流程完成 |
| 06 分支 | 默认 Notebook 无异常完成 | 指定边界分叉与请求对照完成 |
| 07 Skill 提炼 | 默认 Notebook 无异常完成 | 格式检查及同题加载对照完成 |
| 08 渐进披露 | 默认 Notebook 无异常完成 | 正文、参考、脚本执行观察完成 |
| 09 隔离与审批 | 默认 Notebook 无异常完成 | 当前宿主的写入边界与批准 / 拒绝对照完成 |
| 12 Memory 修订 | 正常 Turn、replace 成功回执、精确文件变化均通过 | 明确指令下的版本修订；不验证自主学习 |
| 18 付款 Eval | 8 次请求完整，before=4/4、after=4/4 | 本次无通过率增益，不支持优化有效的结论 |

九份 Notebook 默认可选开关均关闭。执行无异常包括各格当前显式检查，不等于增加了全任务语义评分；第 04、05、07 等章的判断质量仍按正文观察题审阅。第 09 章没有独立网络隔离探针，不能把配置值称为网络验证结果。

接口探针原始摘要见 [model-probe.json](validation/model-probe.json)，Notebook 运行摘要见 [notebook-runs.json](validation/notebook-runs.json)。付款汇总见 [payment-eval-summary.json](validation/payment-eval-summary.json)。文件哈希用于核对本次证据对应的源，不提供身份认证。

## Memory 失败案例也进入课程

最初的大工具集合运行实际写入 7.2，但在 6 次迭代耗尽后返回摘要，不能算正常完成。收紧到 memory 工具后，另一次正常 Turn 把版本改对，却删除了 `cache-01`；也有批量 replace 只写入 `Redis 7.2` 并追加测试探针的失败。成功写入回执没有保护条目中仍然有效的其它事实。

修复把新条目完整内容作为明确任务约束，说明 replace 是整条替换，并增加三层验收：正常 Turn、单次或批量 replace 的配对成功回执、只替换版本号的实际内容。当前真实运行通过；失败不被改名为通过，也不用于宣称模型自动修订可靠。这个案例直接说明了代码为何需要回读与内容检查。

## 离线与来源检查

`make verify` 运行源码/Notebook 编译、JSON/YAML、shell、Ruff、章节与本地链接检查及回归。API 测试使用显式夹具，不伪装真实模型；真实模型结果单独列在上表。开发环境使用哈希锁文件，上游源码使用 [deps.lock.json](../deps.lock.json)，Hermes 实跑对应 `aaf9688519cca58dd5f76a589a0911aff269b060`。

本次本地最终结果：141 项测试通过；121 个 Python 文件、9 份 Notebook 的 49 个代码格、133 份 JSON、18 份 YAML 和 13 份 shell 通过格式或语法检查。章节检查覆盖 23 章主线及 Assembly、Capstone、快照附录，共 26 个契约；237 个本地链接均存在，文件链接也核对了是否随仓库发布。

章节契约检测代码入口、Python 符号与 Notebook 稳定单元 ID 的变化；它不能自动判断中文解释是否准确，因此还需沿实际调用、返回和文件读写人工核对。运行摘要中的哈希对应执行时文件；后续文字修订或凭证读取去重不会将旧运行变成新运行。Memory 摘要已用最终修订代码重新执行并更新。

后续逐份复核增加了九个 Notebook 的 Markdown 推理小结，并将第 01 章一条 GLM 凭证提示改为 DEEPSEEK_API_KEY。当前整份 Notebook 哈希因此与本页旧执行源不同；其余代码单元保持，具体差异在新复核记录中列出，不将旧执行摘要改写为新版本实跑。

远程离线检查由 `.github/workflows/offline.yml` 在 Ubuntu 24.04 / macOS 14 上运行。应按远程提交的 SHA 核对 Actions 结果；本地通过不代替远程 CI，本轮真实模型运行也不会在 CI 中重复。

## 当前未覆盖

本轮没有将 11、13–17、19–23、Assembly 与 Capstone 的全部付费路径逐一执行；其原生接口、候选、采用与跨实例结果不能借前九章或 Memory / Eval 的通过补齐。旧 GLM 运行描述属于原场景与版本，不是本轮 DeepSeek 验证。

Linux CI 只检查离线行为；第 17 / 19 章的 macOS 沙箱、跨主机共享、生产鉴权与分布式发布补偿需要单独验收。第 20 章独立留出没有在本轮消耗，Capstone 新订单保留题未运行；没有统计泛化或生产收益结论。
