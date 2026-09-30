# 第21讲：离线程序记忆

主入口 `run21.py` 使用原生 SkillClaw 代理采集真实 Hermes 的4个任务，另设1个直连对照，再以一次原生 `EvolveServer.run_once` 生成隔离候选。正式加载目录继续保留旧版，不发布、不采用。

```bash
export GLM_API_KEY=...
.deps/hermes-agent/.venv/bin/python -B examples/21-skillclaw-session-collection/run21.py
```

依赖固定 Hermes `aaf9688519cca58dd5f76a589a0911aff269b060`、SkillClaw `bf4dc2ee9430ecffb60e19630d26f57dfa2bd326`，在各源码目录按上游依赖安装。可用 `HERMES_SRC`、`SKILLCLAW_SRC` 指定同版源码。只读显式环境凭证，不自动加载个人 `.env`。模型为glm-5.2，业务记录为教学快照。

每任务最多6轮、2200输出Token，150秒超时；离线管线240秒、每次提炼调用最多12000输出Token。任务不重跑，但Hermes与代理存在原生传输重试。无总截止，费用以账单为准。缺凭证直接停止，本轮不会伪造候选或统计。

## 核对三份材料

`output/lecture21/<编号>/offline/summaries.json` 保存原生摘要，`skill-groups.json` 保存按实际Skill归组的会话编号，`candidates/<job_id>/SKILL.md` 和 `candidate.diff` 保存真实生成文件及差异。`candidate-evidence.json` 给出路径与哈希；无不同候选时脚本失败，不能把空文件当作生成成功。

`formal/service-diagnosis/SKILL.md` 是旧版，`summary.json` 核对其哈希。代理先关闭会话、等待上传，再执行离线管线；本机共享仅作隔离材料输入。`validated` 只产生待验证工作，不启动验证程序。摘要、归组和差异用于观察，不代表候选可靠或已采用。

统计只用本轮 `signal-audit.json`：异常线索由实际命令和快照一致性判断，不从模型结论推定根因。模型请求行数不是会话数；原生skill_view新增调用后，记录行数须读本轮文件，不能套用旧练习的8行。三次上游任务复用快照，不是三个独立业务样本。

观察题：摘录摘要、Skill归组、候选差异各一处，解释它由哪条真实工具回执支持；核对候选文件与正式旧版哈希。未完成时记录最后阶段与错误。思考题：为什么不在请求返回前直接覆盖Skill？

旧 `session_collection.py` 和 `run.sh` 是另一接入示例：自建GLM循环加原生SkillHub上传。手写轨迹只存 `output/constructed-reference/`，不上传；清单标明来源，学习前仅保留真实会话。旧输出不是主练习统计，不与新轮拼接。
