# 补充实验：方法回退与会话历史恢复

[上一节](../19-enterprise-dataset/README.md) · [下一节](../23-final-assembly/README.md)

## 问题与案例

这是第 19 章旁的补充实验，不代替企业数据集。故意把“处置已受理”改成“服务已恢复”，用同一道 202 Accepted 题探测正确、退化和恢复。三次各自新建模型对话，文件恢复与行为恢复分别检查。Skill 回退应保留执行之后的审计流水；整库时间点恢复则是另一种语义。

## 代码阅读路线

[snapshot_restore.py](snapshot_restore.py) 建完整 Skill 副本与 SHA-256 清单，故意修改方法，核对后恢复全部清单文件并删去多余文件，再检查 SessionDB ID 与消息数未变。补充 export_all / import_sessions 只导入其支持字段到空库，已有 ID 跳过，活动字段重置，不能称为完整物理数据库恢复。

[统一环境与模型准备](../../docs/MODEL_SETUP.md)

本讲主练习为 [企业数据集](../19-enterprise-dataset/README.md)。下面保留恢复实验，不承担数据集转换与两引擎比较。


Skill 每次改动都应留下可查询、可恢复的历史；当 L18 业务评测发现旧任务退化时，取回选定版本。SessionDB 会话历史备份只是补充实验。

## 运行方式

从仓库根目录执行，先按根 README 准备 Python 3.12 的 Hermes 环境，再运行：

```bash
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
export DEEPSEEK_API_KEY='<在本机填写，不要写入文件或提交>'
.deps/hermes-agent/.venv/bin/python examples/19-snapshot-restore/snapshot_restore.py
```

只从显式环境 DEEPSEEK_API_KEY 读取凭证。

脚本在网络调用前主动清理 6 个代理环境变量，直连 `https://api.deepseek.com`。完整运行调用 3 次 `deepseek-flash`，每次使用新的 `messages` 列表，参数为 `temperature=0, max_tokens=1024, extra_body={"thinking":{"type":"disabled"}}`，不携带上一轮对话。

## 主例流程（9 步）

1. 在 `tempfile.mkdtemp()` 创建的 `LAB_HOME/skills/oncall-triage/` 中写入带 YAML frontmatter 的 `SKILL.md` 和 `references/oncall-records.md`。
2. 改动前用 `shutil.copytree` 复制整个 Skill 目录到 `LAB_HOME/snapshots/v1-受理成功不等于恢复/`，建立包含版本、标签和各文件 SHA-256 的 `manifest.json`。清单不计算自身哈希；快照不含 `state.db`。
3. 将 v1 的 Skill 和参考文件全文拼入 system prompt，用告警 INC-2026-09 中 search-api 重新加载请求的 `202 Accepted` 场景探测旧行为，预期 `VERDICT=INDETERMINATE`。
4. 确定性替换第 2、3 条规则，把处置请求受理成功错误地当作服务已恢复，得到 v2。原文未唯一匹配则立即报错。
5. 比较快照与当前 `SKILL.md`，输出 `v1_before` 到 `v2_after` 的 unified diff，并保存 2 个版本副本。
6. 在新对话中用同一探针测试 v2，预期 `VERDICT=SUCCESS`。只有基线正确、v2 错误时才打印“已观测到退化”。随后在 `LAB_HOME/state.db` 写入 2 个会话、各 2 条消息，记录 ID 和消息数并关闭数据库。
7. 读取选定 v1 的清单，先检查快照哈希，再复制清单内所有文件回 Skill 目录，删除清单外的多余文件，逐文件复核恢复后的 SHA-256。此步骤只修改 Skill 目录，不操作 `state.db`。
8. 用恢复后的 Skill 和参考文件重新建立对话，预期回到 `VERDICT=INDETERMINATE`。只有完整观测到“正确、退化、恢复”才打印“旧行为已恢复”。
9. 重新打开主例 SessionDB，断言仍是 2 个会话、原 ID 均在、每个会话的消息数及总数未变，证明 Skill 回退不删除快照之后的会话记录。

回答只含 `VERDICT=INDETERMINATE` 时记为旧行为，只含 `VERDICT=SUCCESS` 时记为错误行为。缺少标记或同时出现 2 个标记时，保存并原样打印回答，记为“无法判定”，继续执行。文件哈希恢复通过不等于模型行为验证通过，真实结果以本次回答为准。

## 补充实验：会话历史备份（不是本讲主例）

主例结束后，另建临时目录和 `history.db`，写入 2 个会话、各 2 条消息。用 `export_all()` 导出并保存 JSON，关闭连接后删除该临时数据库及其 WAL/SHM 文件，再向空库调用 `import_sessions(exported)`。打印返回的 `ok/imported/skipped/detached`，随后再导入 1 次，验证已有 ID 被跳过。

`export_all()` 返回会话行字段（包括 `id/source/model/title/started_at` 等）及 `messages`（包括 `role/content/timestamp` 等），不是整个数据库所有表、所有列的物理备份。**本仓库当前 Hermes 实现还包含 `last_activity_at/last_activity_description/last_activity_provenance` 等活动字段，不能写成“不包含运行时心跳列”。** `import_sessions()` 对新导入的会话把这些字段重置为 `NULL`；已有 ID 直接跳过，不覆盖已有记录。

`import_sessions()` 的语义是把会话历史导入库里，不是让整库回到某一时刻。要用导出历史重新建立当时的会话集合，需自行先清空目标库，再导入空库；即便如此，未导出的状态及被重置的运行时状态仍不会复原。本实验只清空补充实验的临时库，不触碰主例 `state.db`。

复原比对严格限于会话 `id/source/title/model/started_at` 与每条消息 `role/content/timestamp`，另外检查重复 ID 跳过和活动字段重置。结论为：**导出字段在空库导入后可复原；已有 ID 会被跳过，运行时字段被重置。** 接口依据是 `.deps/hermes-agent/hermes_state_portability.py` 和 `hermes_state.py`。

## 本场景的预期观察（以实际运行结果为准）

预期路径为 `INDETERMINATE → SUCCESS → INDETERMINATE`，3 次均为独立新会话。已有输出来自场景替换前的运行，不能作为当前 On-call 场景的实跑结果：

- 基线 v1：预期模型首行 `VERDICT=INDETERMINATE`，说明 202 Accepted 仅代表处置请求已受理，不等于服务恢复，需继续复查监控观测。
- 改坏 v2：同一题预期模型首行 `VERDICT=SUCCESS`，按改坏后的规则直接判定服务已恢复；只有实际复现退化时，脚本才打印“已观测到退化”。
- 取回 v1 并逐文件核对 SHA-256 后：预期新会话首行回到 `VERDICT=INDETERMINATE`；完整观测到恢复时，脚本才打印“旧行为已恢复”。
- SessionDB：断言恢复前后均为 2 个会话、4 条消息，Skill 回退未删会话记录。
- 补充实验：空库导入 `imported=2, skipped=0`；重复导入 `imported=0, skipped=2`，运行时活动字段被重置为 NULL。

当前 Chat 循环关闭 thinking；旧模型的输出预算现象不作为当前结果。

## 产物文件

持久化演示产物写入本讲 `output/`，相对仓库根目录如下。同名文件在再次运行到对应步骤时覆盖；中断后的目录可能包含上 1 次运行的文件，应结合本次终端输出判断。

| 路径 | 内容 |
| --- | --- |
| `examples/19-snapshot-restore/output/snapshot_manifest.json` | v1 版本、标签及 Skill 全目录文件的 SHA-256 清单 |
| `examples/19-snapshot-restore/output/probe_v1_old.txt` | v1 基线的模型原始回答 |
| `examples/19-snapshot-restore/output/skill_diff.txt` | v1 与 v2 的统一差异文本 |
| `examples/19-snapshot-restore/output/v1_SKILL.md` | 改动前的 Skill 正文副本 |
| `examples/19-snapshot-restore/output/v2_SKILL.md` | 退化版的 Skill 正文副本 |
| `examples/19-snapshot-restore/output/probe_v2_broken.txt` | v2 的模型原始回答 |
| `examples/19-snapshot-restore/output/restored_SKILL.md` | 恢复后的 Skill 正文副本 |
| `examples/19-snapshot-restore/output/probe_restored.txt` | 恢复后新会话的模型原始回答 |
| `examples/19-snapshot-restore/output/sessiondb_backup.json` | 补充实验的会话历史导出 |

终端还会打印主例和补充实验的临时目录。完整 Skill、含 references 的 v1 快照、快照内的 `manifest.json`、主例 `state.db` 及补充实验 `history.db` 位于这些独立临时目录，便于运行后检查。`output/` 中的正文副本用于查看；实际目录恢复使用临时目录里的完整 v1 快照。

## 工程应用与观察练习

本讲临时目录保留供核对，但 output 顶层可被下一次覆盖，因此应按终端本次目录与三份原始回答判断。缺标记或同时出现两个 VERDICT 时记无法判定，文件哈希一致不补足模型行为证据。生产恢复进一步需要一致性快照、版本协议、并发写入处理和审计保留。

选一个用例，保存完整输入、版本、原始输出与评分。注入缺失回执或错对象的反例，说明它在哪一层被拒绝；若未拒绝，保留为待修复问题。
