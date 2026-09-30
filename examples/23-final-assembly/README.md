# 第23讲：受控巡检参考实现

在线值守查询教学快照，保存真实GLM工具循环与SessionDB；SkillClaw workflow同步提炼候选，隔离评测决定采用版，C以新会话拉取方法继续巡检。它没有运行原生Hermes前台或后台复盘，不能宣称各层原生能力均已集成。

```bash
export GLM_API_KEY=...
.deps/hermes-agent/.venv/bin/python -B examples/23-final-assembly/final_assembly.py
```

使用课程 Hermes/SkillClaw 固定源码与依赖（提交见第21讲README）。凭证来自环境；只读部署、配置、指标、日志工具返回教学快照，时间为模拟逻辑分钟。前台是自建 `GlmToolLoopAgent`，workflow同步等待候选；运行会产生模型费用。每题工具循环及裁判分别调用模型，裁判最多2500输出Token；脚本尚无整轮费用上限或全局截止，不作耗时承诺。

## 接入情况

| 部件 | 本轮情况 |
|---|---|
| 在线工具循环 | 自建GLM循环，四类只读巡检工具 |
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

两张巡检专用图见 `_review/figs/23-mechanism.svg`、`23-integration.svg`。
