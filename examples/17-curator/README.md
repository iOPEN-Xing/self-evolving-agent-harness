# 第 17 章：管理生命周期，整合重复方法

[上一节](../16-skill-incremental-patch/README.md) · [下一节](../18-business-eval/README.md)

## 问题与案例

技能增多会增加常驻描述与选择困难。本章将确定性的生命周期转换和模型参与的整合分开，长期未用不等于低价值，归档也不是删除。第一组按 1、10、20 天未活动与 pinned 保护比较；第二组整合单笔付款方法，要求保留旧渠道“受理流水号映射渠道交易号”的特例。它重新构造材料，不是自动接续上一章候选。

## 代码阅读路线

[run_lab17.py](run_lab17.py) 的 `main` 调用真实 skill_manage 与 apply_automatic_transitions，无模型请求。[run_lab17_consolidate.py](run_lab17_consolidate.py) 的 `prepare_sandbox` 先探测写入边界，`worker` 调用原生 Curator，`skill_manage_operations` 和 `verify` 核对吸收、归档与可达资料；`minimal_decision` 只记录完整流程失败后的判断。

[统一环境与模型准备](../../docs/MODEL_SETUP.md)：Python 3.12、deepseek-flash、官网直连。

本目录分别核对生命周期转换和重复 Skill 整合。前者是确定性规则，后者会调用真实模型；两项分开运行、分开验收。

## 运行

从专栏根目录执行：

```bash
bash examples/17-curator/run.sh lifecycle
bash examples/17-curator/run.sh consolidate
```

整合练习使用 `DEEPSEEK_API_KEY` 环境变量中的凭证，默认模型为 `deepseek-flash`。脚本在请求模型前清除代理变量，不把凭证写入配置。生命周期练习不调用模型，不要求凭证。

默认使用 `.deps/hermes-agent/.venv/bin/python`，可通过 `HERMES_SRC` 指定依赖目录；整合练习可通过 `DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL` 调整模型连接。每次运行都创建独立且保留的 `HERMES_HOME`，不会复用上次 Skill 或使用状态。整合练习目前仅验证了 macOS 的 `sandbox-exec` 后端；Linux 等其他平台尚未提供经过验证的运行入口，脚本会退出，不会改为无写入限制的运行。

## 生命周期转换

脚本通过真实 `skill_manage` 创建 4 个练习 Skill，标记为受 Curator 管理，并写入构造的使用记录：

| Skill | 最近活动时间 | 是否固定 |
|---|---|---|
| active-debugging | 1 天前 | 否 |
| stale-log-analysis | 10 天前 | 否 |
| ancient-report-format | 20 天前 | 否 |
| pinned-important | 20 天前 | 是 |

本练习将 `stale_after_days` 设为 7 天、`archive_after_days` 设为 14 天；`stale` 表示长期未活动状态。这是练习配置，不是根据本次任务得出的通用最佳值。

随后直接执行原生 `apply_automatic_transitions(now=now)`。自动核对状态、转换计数和文件内容：活跃对象保留，10 天未用的对象标记为 `stale`，20 天未用的普通对象移入 `.archive/`，固定对象仍为 `active`。

归档后，原目录不再处于活动技能库中，但归档文件逐字保留；其余 3 份正文也逐字未变。该过程是真实执行，不是预览，也不是永久删除；本项没有实际调用恢复操作。

结果摘要在 `output/curator_transitions.json`，独立状态与记录保存在 `output/lifecycle_runs/`。

## 原生 Curator 整合

脚本在新的空库中建立 2 份构造的单笔足额支付调查 Skill。共同方法相同，旧渠道 Skill 另有一条关键特例：先把受理流水号映射为渠道交易号，再查最终付款状态；映射缺失或匹配多条时不能猜测。这是独立的整理练习，不会自动读取其他练习修订出的分次付款方法，不能据此声称跨阶段自动流水线已经跑通。

脚本调用原生 `run_curator_review(consolidate=True, synchronous=True)`，未启用预览模式，由真实模型决定并通过工具执行整合。完整流程最多运行 300 秒。若完整流程出错，只再请求一次最小整合判断；这个后备步骤不代替模型合并文件，也不算完整验收通过。

验收同时检查：

- 工具操作明确声明将 `payment-legacy-channel` 吸收到 `payment-status-investigation`。
- 旧 Skill 已从活动目录移除，`.archive/` 内仍保留原 Skill，状态为 `archived`。
- 主 Skill 仍链接通用支付说明，并能沿引用找到旧渠道映射说明。
- 可达说明中保留所需映射字段、先映射后查询的顺序，以及缺失或多条匹配时的处理。

原生 Curator 可以使用技能工具和 Terminal，工作目录本身不限制它向其他目录写入。本练习因此在原生 worker 外增加 macOS 的 OS 写入限制：整个进程及其后代只能向本次运行目录写文件，另允许写入空设备 `/dev/null`；保留读取与网络能力，不改系统 `HOME`。临时文件与缓存目录放在本次目录内。

模型启动前，会用单独准备的探针核对：目录内写入允许，目录外新建、覆盖、重命名和经过符号链接写入均被拒绝。只有全部通过才启动模型。该限制来自本练习启动器，并非 Hermes 原生能力，也不表示 `skill_manage` 的归档和审批规则会自动约束任意终端命令。

结果保存使用 `O_NOFOLLOW`，不跟随输出文件的符号链接。完整流程超时时会停止主进程组；Terminal 后代可能另起进程组，不能仅凭该操作宣称所有后代都已停止，但它们仍继承 OS 写入限制。

只有 `consolidation_verified=true` 且进程退出码为 `0` 才表示本例的文件验收通过。`real_llm_consolidate_run=true` 只说明完整模型流程结束，不等于已经完成整合。

结果摘要在 `output/consolidate_result.json`，模型原文、工具记录、前后文件和使用状态保存在 `output/consolidate_runs/`。顶层摘要不改写模型原始报告，原文通过 `model_report_file` 指向本次 `run.json`。

最后应对照完整文件审阅共同方法与特例是否都保留。关键词和文件检查不能替代语义审阅或真实业务验证；这个练习没有执行支付查询，也没有验证长期使用效果。

## 配置与触发边界

为单独观察整理机制，本练习重新构造单笔支付方法，不自动接续前面的修订结果。生命周期脚本直接调用状态转换；整合脚本显式传入 `consolidate=True`。它们不验证客户端或服务自动调度，`automatic_scheduling_verified=false` 只表示这条路径尚未检查。

实际使用时，核对当前版本与配置中的 `stale_after_days`、`archive_after_days`、启用与暂停状态、维护间隔和上次运行状态，再核对所用入口何时检查触发条件。7/14 天是练习设置，不是通用默认期限。长期未活动状态不等于低价值，固定保护与管理范围也参与转换判断。

名称与描述会增加常驻输入，相近描述会增加选择困难；描述过长还可能截掉关键适用条件。具体 token 开销要按实际文本和模型测量，不提供未经计数的估算。

## 在学习循环中的作用

Curator 管理方法何时保留、归档或整合，帮助控制经验库的体积和适用性。状态转换与合并成功属于维护结果；整理后的方法是否改善任务，要交给业务 Eval 检查。

## 工程应用与观察练习

整合检查共同方法、例外条件、引用可达性与归档保留；关键词命中不能替代语义评测。生命周期与模型整理都需要作业编号、期限和暂停开关。显式调用不证明自动调度有效。macOS 写入 Sandbox 属于课程外层启动器，不能归为 Hermes 原生权限；终止主进程组也不证明另起组后代都结束。

按相同观察时点计算 1/10/20 天样本的状态，加入 pinned 后核对哪些转换应被阻止。若完整整合失败，分别保存失败与最小判断结果，再确认正式方法目录是否保持原内容。
