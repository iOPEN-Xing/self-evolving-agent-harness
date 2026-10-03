# 受控巡检：连接状态机、候选、评测与恢复

[上一节](../22-skillclaw-shared-revision/README.md) · [下一节](../capstone/README.md)

## 问题与案例

前面拆开各项机制，本章用巡检参考实现将它们连接。模型查教学快照，观察器按当前窗口产生告警或恢复事件，状态机再用实际查询与报告推进。整体指标正常不能掩盖单实例异常，202 Accepted 只表示处置请求已受理，连续正常观测与恢复仍需验证，服务恢复也不自动关闭根因未明问题。

## 代码阅读路线

[final_assembly.py](final_assembly.py) 的 `ToolLoopAgent` 是自建真实 DeepSeek 工具循环；[shift/runtime.py](shift/runtime.py) 的 ObservationTools 检查服务与当前逻辑时刻，poll_sensor 只由已见观测产生事件；[versioning/policy.py](versioning/policy.py) 核验完整证据与评分；[versioning/snapshot.py](versioning/snapshot.py) 在恢复前验证清单、文件和临时数据库。

[统一环境与模型准备](../../docs/MODEL_SETUP.md)

在线值守查询教学快照，保存真实DeepSeek工具循环与SessionDB；SkillClaw workflow同步提炼候选，隔离评测决定采用版，C以新会话拉取方法继续巡检。它没有运行原生Hermes前台或后台复盘，不能宣称各层原生能力均已集成。

```bash
# 先按 MODEL_SETUP 在当前终端设置 DEEPSEEK_API_KEY
.deps/hermes-agent/.venv/bin/python -B examples/23-final-assembly/final_assembly.py
```

使用课程 Hermes/SkillClaw 固定源码与依赖（提交见第21讲README）。凭证来自环境；只读部署、配置、指标、日志工具返回教学快照，时间为模拟逻辑分钟。前台是自建 `ToolLoopAgent`，workflow同步等待候选；运行会产生模型费用。每题工具循环及裁判分别调用模型，裁判最多2500输出Token；脚本尚无整轮费用上限或全局截止，不作耗时承诺。

## 接入情况

本轮工程回归见 [运行手册](../../docs/RUNBOOK.md)。依赖准备脚本现按 deps.lock.json 获取固定提交；历史实跑与本次验证分开解读。后台演化作业默认时限 900 秒，可由 `EVOLVE_JOB_TIMEOUT_SECONDS` 调整；仍未实现整轮费用上限。

快照改为 `snapshots/<version>/{manifest.json,state_snapshot.json,SKILL.md}`，完整核验后才恢复；旧快照需要重新生成。时间点恢复会移除后续会话，仅用于本讲演练。生产 Skill 回退应保留流水。工程检查可在根目录 `make verify` 无密钥运行。

| 部件 | 本轮情况 |
|---|---|
| 在线工具循环 | 自建DeepSeek循环，四类只读巡检工具 |
| 原生后台复盘 | 未运行；同步等待SkillClaw workflow |
| 陈述性记忆、旧会话检索、外部Provider | 未接入 |
| Skill提炼 | 原生workflow一次，validated候选隔离 |
| 上下文渐进披露、规则审批 | 自建read_skill读取；只读工具白名单，无原生审批流程 |
| Curator、GEPA | 未接入本轮 |
| 评测、版本决定、共享 | 定量回执+模型裁判，评分异常REJECT，SkillHub本机后端只分发采用版 |
| 快照恢复、跨实例续接 | A本地数据库/方法恢复；C新会话，不继承A调查 |

## 输出与历史记录

每轮保存到 `output/oncall-runs/<编号>/`，`latest_oncall_run.txt` 指向最新一轮。`review_input.json` 分别保存 `service_recovered` 和结构化 `open_questions`（问题编号、描述、依据引用、下一项核查）；服务恢复不清空根因未明的问题，后台收到这些未决材料。

`eval_report.json` 与 `decision_log.json` 逐项保留裁判原始输出。汇总分别列出通过、有效评分下的不通过、缺失评分；缺失不补0或合理分数。根目录 `output/eval_report.json` 是历史巡检报告的重新分类摘要，provenance明确标明来源、不是新模型运行；原支付摘要另存history。

已核历史 `20260930-082310-1f68b9` 中，基线记录3/6、候选记录5/6；3项裁判解析失败：v0/NI-01、v0/HO-02、v1/RG-01。缺失与有效失败分开，不能据这组汇总断言候选能力提升。决定REJECT，保留v0。该历史源指纹与修改后代码不同，不能充作本次代码的联网验收。

快照演练恢复A的2个会话、52条消息；C只拉取采用版，以独立数据库和新会话巡检。未验证跨实例续接原调查，也未证明下一轮因候选而改善。

## 自查与题目

```bash
.deps/hermes-agent/.venv/bin/python -B -m unittest discover -s examples/23-final-assembly/tests -v
```

观察题：核对候选与正常加载目录、3类评分结果、本地恢复记录和C新会话，确认ADOPT或REJECT的依据。思考题：缺失评分为什么不能补成一个合理分数？候选生成、评测拒绝和基线继续服务是本轮路径，不把它写成已经证明成长。


## 控制器为什么保留模型之外的状态

模型提出判断，状态机核对判断能否由当前工具观察支持。例如 202 Accepted 支持受理状态，连续正常监控与复查支持恢复；根因仍未知时，open_questions 继续保留。这样复盘能学习未决问题，而不会只看到一份已经写成成功的报告。

采用策略还需检验评分完整性。缺失评分没有业务含义，不能补成失败分再与候选相减，也不能略过它求平均。源指纹、用例与原始裁判绑定后再重算，能阻止不同轮材料混合进入一次决定。正常 REJECT 表示控制器保留依据和现版，不表示候选完成了成长。

## 在学习循环中的作用

本章将经历、候选、独立评分和版本决定接成受控巡检参考实现。前台执行来自自建工具循环，评分异常会保留现版。后续 C 使用采用版独立运行；是否因为新方法而改善，继续核对具体采用决定和成对业务结果。

## 面试讲述与追问

受控巡检要避免模型一句“恢复了”同时推进状态与发布新方法。我把当前观测、模型建议和控制器状态分开，以连续正常窗口与真实复查支撑恢复，再让完整独立评分决定候选。历史 3/6 与 5/6 包含三个缺失裁判，最终 REJECT，不能据此宣称改善。前台是自建 DeepSeek 循环，SkillClaw 同步提炼，原生 Hermes 后台没有接入这条路线。

**追问：缺失评分补成 0，是否更保守？** 缺失是未能取得有效判断，0 是有效判断下的失败，两者含义不同。补分会改变成对比较和采用依据；应单列缺失并拒绝当前证据，修复后用新轮次重测。

讲述时可打开 [final_assembly.py](final_assembly.py)、[runtime.py](shift/runtime.py)、[policy.py](versioning/policy.py) 核对实现或原始记录；个人贡献与结果的表述规则见 [项目面试指南](../../docs/INTERVIEW_GUIDE.md)。

## 工程应用与观察练习

事件、模型建议、控制器状态分别记录。工具预算在分发前计数，错误与超额不能借一份已提交报告推进状态。恢复事件要求当前且连续正常窗口至少 10 分钟，并有实际指标复查。候选正文、支持文件、源指纹、case 和原始裁判都进入采用判断；评分解析失败按缺失处理，不能补成合理分数。SkillHub 只分发采用版。

沿一个候选的版本哈希回查原始裁判、确定性重算与决定。若有缺失评分，确认它没有被补成零分；再核对 C 加载的正式版，解释为何一次完整 REJECT 可以体现控制流程正常结束。
