# 会话采集：让真实经历形成可审阅候选

[上一节](../20-gepa-offline-optimization/README.md) · [下一节](../22-skillclaw-shared-revision/README.md)

## 问题与案例

离线优化需要数据，这一章转向在线经历采集。v1 只查服务健康与上游延迟；四个真实 Hermes 任务通过本机 SkillClaw 代理，其中三次上游任务复用同类快照，另一次连接池条件产生未解决线索。直连对照绕过代理，应该不出现在代理会话池中。采集范围和来源必须先证明，再讨论提炼。

## 代码阅读路线

[run21.py](run21.py) 启动原生代理并关闭上传会话；[learning_worker.py](learning_worker.py) 用原生 skill_view 与教学只读工具；[audit21.py](audit21.py) 从命令回执核对快照；[evolve_once.py](evolve_once.py) 调用原生 EvolveServer.run_once；[candidate_evidence.py](candidate_evidence.py) 将真实候选落盘并核对差异。

[统一环境与模型准备](../../docs/MODEL_SETUP.md)

主入口 `run21.py` 使用原生 SkillClaw 代理采集真实 Hermes 的4个任务，另设1个直连对照，再以一次原生 `EvolveServer.run_once` 生成隔离候选。正式加载目录继续保留旧版，不发布、不采用。

```bash
# 先按 MODEL_SETUP 在当前终端设置 DEEPSEEK_API_KEY
.deps/hermes-agent/.venv/bin/python -B examples/21-skillclaw-session-collection/run21.py
```

依赖固定 Hermes `aaf9688519cca58dd5f76a589a0911aff269b060`、SkillClaw `bf4dc2ee9430ecffb60e19630d26f57dfa2bd326`，在各源码目录按上游依赖安装。可用 `HERMES_SRC`、`SKILLCLAW_SRC` 指定同版源码。只读显式环境凭证，不自动加载个人 `.env`。模型为deepseek-flash，业务记录为教学快照。

每任务最多6轮、2200输出Token，150秒超时；离线管线240秒、每次提炼调用最多12000输出Token。任务不重跑，但Hermes与代理存在原生传输重试。无总截止，费用以账单为准。缺凭证直接停止，本轮不会伪造候选或统计。

## 核对三份材料

`output/lecture21/<编号>/offline/summaries.json` 保存原生摘要，`skill-groups.json` 保存按实际Skill归组的会话编号，`candidates/<job_id>/SKILL.md` 和 `candidate.diff` 保存真实生成文件及差异。`candidate-evidence.json` 给出路径与哈希；无不同候选时脚本失败，不能把空文件当作生成成功。

`formal/service-diagnosis/SKILL.md` 是旧版，`summary.json` 核对其哈希。代理先关闭会话、等待上传，再执行离线管线；本机共享仅作隔离材料输入。`validated` 只产生待验证工作，不启动验证程序。摘要、归组和差异用于观察，不代表候选可靠或已采用。

统计只用本轮 `signal-audit.json`：异常线索由实际命令和快照一致性判断，不从模型结论推定根因。模型请求行数不是会话数；原生skill_view新增调用后，记录行数须读本轮文件，不能套用旧练习的8行。三次上游任务复用快照，不是三个独立业务样本。

观察题：摘录摘要、Skill归组、候选差异各一处，解释它由哪条真实工具回执支持；核对候选文件与正式旧版哈希。未完成时记录最后阶段与错误。思考题：为什么不在请求返回前直接覆盖Skill？

旧 `session_collection.py` 和 `run.sh` 是另一接入示例：自建DeepSeek循环加原生SkillHub上传。手写轨迹只存 `output/constructed-reference/`，不上传；清单标明来源，学习前仅保留真实会话。旧输出不是主练习统计，不与新轮拼接。

## 学习材料的数量与独立性

代理会话说明哪些请求经过采集入口，工具回执说明它们实际观察了什么。重试会增加请求行数，重复快照会增加任务数，却不一定增加独立业务信息。将这些都算成新案例，会夸大一种模式得到的支持。

摘要与归组是有损整理，应保留源会话编号，方便核对遗漏或错误概括。候选 diff 还需追到具体事实或纠正；模型额外建议不能自动归为已验证知识。本章不启动验证程序，隔离候选停在待检状态，正式旧版是清楚的比较参照。

## 在学习循环中的作用

多实例经历进入同一学习管线，但任务数、请求数与独立业务事件数需要分别统计。本章到待验证候选为止，正式目录保留旧版；下一章继续观察发布设置、接收端加载和行动差异。

## 面试讲述与追问

采集的价值在于把实际执行经历交给学习端，并保留来源。我用代理覆盖任务与直连对照区分采集范围，再沿会话编号检查摘要、归组和候选差异。相比统计请求行数，这样更容易发现重试与重复快照放大样本的问题。该入口只形成隔离待验证候选，正式 v1 不变；四个任务中重复上游快照提供的独立业务信息有限。

**追问：四个任务、更多请求行，能否说明学习数据更丰富？** 先区分传输重试、任务和独立业务事件。三次同类快照可能只覆盖一种条件；要增加信息，应增加不同业务状态和明确纠正，并保留各自来源。

讲述时可打开 [run21.py](run21.py)、[audit21.py](audit21.py)、[candidate_evidence.py](candidate_evidence.py) 核对实现或原始记录；个人贡献与结果的表述规则见 [项目面试指南](../../docs/INTERVIEW_GUIDE.md)。

## 工程应用与观察练习

先数本轮会话，再数请求行、任务和独立业务事件，避免把重试或重复快照放大成样本数。validated 只产生待验证候选，本例没有启动验证程序，正式 Skill 保持 v1。候选差异说明有文件生成，不说明方法可靠。本机代理转发到官网是采集组件；课程不使用第三方模型中转服务。

沿 signal-audit 找到连接池条件的实际查询，把该会话编号追到归组和候选 diff。再核对直连任务没有进入代理采集，并说明三个重复上游快照为何只提供有限的独立业务信息。
