# 综合实践：跑完一次学习循环，再决定下一次采用什么

主练习为原生 Hermes 支付查询总装：在线工具查询付款记录，SessionDB 保存经历，原生后台复盘修订教学方法，候选隔离后作成对比较，再决定正式版、验证共享与副本恢复。巡检 `capstone.py` 保留为另一示例，见 [历史说明](README-oncall.md)。

前面的参考实现用巡检产生经历。这次把在线工具换成支付查询，把业务评测换成付款状态核查，保留经历保存、后台修订、候选隔离、采用控制和共享验证这些关系。我们要检验的是，同一套学习架构能否在另一类任务中成立。

## 环境与命令

从仓库根运行，使用 Python 3.11、Hermes `aaf9688519cca58dd5f76a589a0911aff269b060` 和 SkillClaw `bf4dc2ee9430ecffb60e19630d26f57dfa2bd326`。依赖参照 `requirements-hermes.lock.txt`；原生源码仍保留在 `.deps`，本入口复用已公开的 `examples/assembly/assembly` 与其教学场景，不需内部 `_review` 路径。版本清单来自已观察环境，不包含个人绝对路径。

```bash
.deps/hermes-agent/.venv/bin/python -B examples/capstone/run_assembly_light.py verify
export GLM_API_KEY=...
.deps/hermes-agent/.venv/bin/python -B examples/capstone/run_assembly_light.py capstone
```

`verify` 不读凭证、不调用模型，检查金额/状态评分及队列前置条件，44项自测通过才继续。完整参数为 `capstone`；内部 `--inside`、`--worker` 供子进程使用。仅显式读取 GLM_API_KEY，不读取备用密钥或自动加载个人.env。默认真实glm-5.2、智谱端点；订单与内部队列规则是教学输入。

## 采用规则与预算

运行前 plan.json 列明：评测记录可信、评分器自测通过、关键用例不退化、候选关键要求全部满足、主组有严格增益、同订单的新问法复测不退化。任一不满足即 REJECT，正式目录哈希保持基线。队列建议还必须由同笔交易processing且渠道已受理支持；不能凭关键词判为合理。

4笔订单的主组与同订单新问法各让基线、候选运行，共16个独立任务；这不是新订单泛化。`new-order-reserve/` 另备3笔改变金额与前置条件的新订单，未参与生成与选择，固定候选后才能单独检验。本批未运行这组保留订单，不声称泛化通过。

整轮1500秒；前台+后台子进程210秒，评测任务150秒，后台等待120秒；前台/评测最多12轮，后台最多16轮，每次输出上限4096 Token。任务不自动重跑，原生网络层仍可能重试。另有前台学习与共享复测请求，任务数量不等于模型请求次数；实际费用按供应商账单核对。

## 实际接入

| 部件 | 主练习情况 |
|---|---|
| 在线工具循环、后台复盘 | 原生Hermes；后台处理真实前台会话快照 |
| Skill修订 | 原生skill_view/skill_manage，完整候选目录隔离 |
| 陈述性记忆 | 后台可能写入；后续任务使用尚未验证 |
| 旧会话检索、外部Provider | 本轮未验证 |
| Curator、GEPA | 未运行 |
| 评测与采用 | 真实Hermes逐题执行、评分器自测与严格采用条件 |
| 共享 | 本机SkillHub；正式采用组与候选复测组隔离 |
| 跨实例经历再学习 | 尚未完成 |
| 快照恢复 | 独立方法副本；未验证跨实例续接调查 |

## 输出、失败与观察

`output/capstone/assembly-runs/<编号>/` 是每轮统一入口；先读 `plan.json`，再读 `stage-status.json`、`trace.json` 与 `completion.json`。六阶段为 smoke、learn、candidate、eval、shared、snapshot。stage-status保存最后完成阶段、全部阶段状态、错误文件及正式方法逐文件哈希；评测与采用决定在对应阶段目录，来源沿trace节点回查。

失败时读取计划和阶段状态，标出最后完成阶段、error.json或子进程日志、正式方法哈希；修复后另建运行编号，不覆盖旧轮，不拼接几轮的成功题。超时日志旁有process.json。缺凭证也保存本轮计划、错误和正式基线哈希。

观察题：从后台实际工具回执核对候选是否生成，再核对成对评分、采用决定、共享接收端回执和副本恢复；任一未通过都追到原始记录。思考题：反复修改直至同一批题全过，为什么仍不能证明应采用？完整答案另行发放。
