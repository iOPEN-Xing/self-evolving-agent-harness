# 第 15 讲：Skill 自主创建

## 练习目标

给模型一段明确标注为构造数据的支付调查经历，以及现有 2 个 Skill 的完整内容，让模型选择新建 Skill、修订已有 Skill 或不更新。模型作出选择后，脚本通过 Hermes 的真实 `skill_manage` 工具保存文件，并回读核对结果。

这里验证的是复盘中的方法选择与文件保存。脚本没有执行支付查询，也没有验证 Hermes 原生后台触发或新会话自动采用 Skill；不能将它描述为已经跑通完整支付业务或自动学习链路。

## 运行

在专栏根目录设置 `GLM_API_KEY` 或 `BIGMODEL_API_KEY` 后执行：

```bash
bash examples/15-skill-autocreation/run.sh
```

默认使用 `.deps/hermes-agent/.venv/bin/python` 和 `glm-5.2`，可通过 `HERMES_SRC`、`GLM_MODEL`、`GLM_BASE_URL` 调整。脚本只从环境变量读取凭证，模型调用前清除代理变量。

每次运行都在 `output/run-*/` 内建立独立且保留的 `hermes-home/`，新运行不会读旧运行的记忆与 Skill。`output/latest-run.txt` 指向本次目录。

## 实际过程

1. 在空目录内种入“日志排错”和“连接池排查”两个已有 Skill，作为模型进行比较的输入。它们不计为本次自主生成。
2. 向真实 GLM 模型提供这 2 个 Skill 全文和构造的支付经历。关键纠正是“请求受理成功”不等于“最终支付成功”；调查还须区分渠道结果、平台结果、通知发送和商户处理。
3. 模型返回 `CREATE_NEW_SKILL`、`PATCH_EXISTING` 或 `NO_UPDATE` 之一，并说明理由。三者均是合法结果，脚本不把不更新改写成创建成功。
4. 新建时，再次调用模型生成 `SKILL.md`，使用真实 `skill_manage(create)` 保存；修订时，让模型给出唯一匹配的补丁，再调用 `skill_manage(patch)`；不更新时保留原有 Skill。
5. 新建分支同时通过 `skill_manage(write_file)` 保存 `references/payment-records.md`。这个参考文件来自脚本预置的练习字段说明，不能冒充模型生成。
6. 回读文件与待写入内容逐字核对，并检查非目标的已有 Skill 没有变化。解析、工具执行或核对失败时以非零状态退出。

## 怎样核对本次结果

先读本次目录下的 `decision.json`，确认 `execution_status`、`decision`、真实模型请求次数、工具操作和 `output_files`。再按本次文件清单检查：

- `conversation.txt`：明确标注来源的构造支付经历。
- `decision_raw.txt`：模型的原始选择与理由。
- `created_SKILL.md`、`created_reference.md`：新建分支实际保存后回读的文件。
- `patch_before_SKILL.md`、`patch_after_SKILL.md`、`patch_diff.txt`：修订分支的前后内容与差异。
- `skills_tree.txt`：本次独立目录内的文件树。

仓库内 `output/` 顶层文件是最近一次核验的结果示例。本机新运行应以 `latest-run.txt` 指向的独立目录为准，不能拿旧示例推断本次创建了什么。

这个练习要观察的取舍是：经验有没有形成可复用方法，现有 Skill 是否已经覆盖它。文件成功保存只证明这次选择得到了执行；方法能否改善后续任务，还需要另外评测。

## 本轮提示修订

生成主文件的提示移除了预先规定的核心查询顺序，只保留文件格式、操作权限与不得虚构接口的要求。模型仍会看见构造的调查过程，但方法怎样组织由模型依据记录判断。脚本预置的 `PAYMENT_REFERENCE` 仍是独立来源，不能把它算成模型自主生成。

旧运行记录对应旧提示，不能据此宣称新提示已运行通过。`native_background_trigger_verified=false` 与 `new_session_adoption_verified=false` 分别表示原生后台触发和新会话自动采用尚未验证；工具保存成功不会改变这两个状态。通过 `/learn` 在前台创建的方法默认由用户掌握，是否允许后台维护须核对来源、所有权与管理状态。
