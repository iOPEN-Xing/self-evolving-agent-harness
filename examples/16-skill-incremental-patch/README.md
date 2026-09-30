# 第 16 讲：Skill 增量修订

## 练习目标

向真实 GLM 模型提供已有的支付状态调查方法，以及新增的分次付款条件，让模型自主选择修订已有 Skill、新建 Skill 或不更新。若选择修订，脚本执行真实 `skill_manage` 操作，保留完整差异并回读核对。

业务经历是明确标注的构造数据。本练习验证方法选择、文件修订及模型如何使用修订后的正文作推演，不执行真实支付查询，不验证 Hermes 原生后台触发或新会话自动采用。

## 运行

在专栏根目录设置 `GLM_API_KEY` 或 `BIGMODEL_API_KEY` 后执行：

```bash
bash examples/16-skill-incremental-patch/run.sh
```

默认使用 `.deps/hermes-agent/.venv/bin/python` 和 `glm-5.2`，可通过 `HERMES_SRC`、`GLM_MODEL`、`GLM_BASE_URL` 调整。脚本只从环境变量读取凭证，并在模型请求前清除代理变量。

每次运行在 `output/run-*/` 下建立独立且保留的 `hermes-home/`；`output/latest-run.txt` 指向本次目录。新运行不会读取旧运行的 Skill 或输出。

## 实际过程

1. 在空库中创建旧版 `payment-status-investigation` 及其 `references/payment-records.md`。旧方法只覆盖单笔足额支付，这两份文件都是明确的起始材料。
2. 给模型提供构造经历：订单应付 10000 人民币分，A 交易的 2000 已成功，B 交易的 8000 仍在处理中。业务允许分次付款，不能用最近一笔成功证明整单付清。
3. 模型选择 `PATCH_EXISTING`、`CREATE_NEW_SKILL` 或 `NO_UPDATE` 并说明理由。选择不更新也是合法结果，脚本不会改成预设的修订。
4. 修订分支让模型给出完整操作序列，先在内存中逐项核对片段唯一匹配及文件结构，再执行真实工具。正文的适用范围和步骤要互相协调，参考文件也要同步更新，不能只在第 1 步末尾追加一句例外。
5. 每次写入后回读，确认内容与本次操作一致；核对 Skill 名称、文件集合、配置文件及分支要求。失败时保留实际输出并返回非零状态。操作按顺序写入，没有自动整体回滚；后续操作失败时，前面已经写入的内容仍可能保留，应对照前后文件处理。
6. 更新完成后，再向模型分别提供单笔足额、分次付款和关键交易结果缺失 3 个条件，请它只说明下一步应查什么。这些回答是文字推演，保留给读者审阅，不等同于实际业务验证。

## 怎样核对本次结果

先读本次 `decision.json`，确认模型实际选择、执行状态、请求次数、工具操作及 `output_files`。再对照这些文件：

- `business_experience.txt`：本次构造经历及来源标记。
- `decision_raw.txt`：模型原始选择与理由。
- `patch_operations_raw.txt`：模型生成的补丁与参考文件内容。
- `patch_before_SKILL.md`、`patch_after_SKILL.md`：正文修订前后内容。
- `patch_before_payment_records.md`、`patch_after_payment_records.md`：参考文件修订前后内容。
- `patch_diff.txt`：两份文件的完整差异。
- `verify_single.txt`、`verify_split.txt`、`verify_missing.txt`：模型按最终正文给出的推演。
- `skills_tree.txt`：本次目录内的实际文件树。

本轮没有走到的分支不能算作已经实跑。仓库中 `output/` 顶层保存最近一次核验的结果示例；本机新运行以 `latest-run.txt` 指向的独立目录为准。

重点检查新条件有没有改变整套方法，同时是否保留了仍然有效的调查步骤与只读边界。文件写入成功并不证明方法已经改善业务表现；两份文件是否协调、3 类推演是否合理，仍须结合差异和真实任务继续检查。

## “先读后改”的验证范围

当前脚本通过 `Path.read_text` 读取主文件和支持文件，把它们作为模型输入；随后脚本在前台调用 `skill_manage` 保存。它没有使用后台来源去验证 `skill_view` 的读取标记，因此 `native_read_before_write_verified` 保持 `false`。

单独检查该机制时，应在隔离且允许后台维护的 Skill 上，先进入后台执行来源，在没有读取标记时请求修改并核对拒绝回执；再通过 `skill_view(name)` 或 `skill_view(name, file_path=...)` 读取准备改动的那个文件后重试，检查实际文件差异。主文件与支持文件要分别读取。只用 Python 读了文件不等于通过后台读取条件；允许维护的所有权也要先确认。

## 文字推演与正式采用

三个 `verify_*.txt` 都是直接提供最终主文件正文后的文字推演。脚本只确认回答非空，`prose_inference_cases` 中的 `semantic_verdict` 仍为“未判定”。需分别人工核对旧场景保留有效步骤、新条件正确汇总、关键结果缺失时保留未知；不能把未知金额计作成功或失败。

`full_method_pack_verified`、`business_effect_verified`、原生触发与新会话采用状态仍为 `false`。完整方法包还包括支持文件和引用；自动发现、加载、实际业务表现及正式采用需要另行验证。旧运行没有 `verify_missing.txt`，不能把新增场景说成已经实跑。
