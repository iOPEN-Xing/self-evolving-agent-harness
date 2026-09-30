# 企业数据集：怎样把真实经历变成可重跑的评测题？

主练习整理教学导出、检查排除原因、生成原生 YAML，校验后使用原手写服务与真实 Hermes 两个自定义执行引擎，再按用例编号配对。快照恢复另见 `../19-snapshot-restore/`，是补充实验。

## 准备

使用 Python 3.11、Go 1.25+ 和 macOS `sandbox-exec`。`dependencies.json` 固定 Skill Up 与 Hermes 提交；Go 依赖由固定提交的 go.mod/go.sum 锁定。补丁只使评分临时目录遵循 TMPDIR。Hermes 所选解释器须已装上游依赖。

在仓库根目录执行：

```bash
export LECTURE_HERMES_SOURCE="$PWD/.deps/hermes-agent"
PYTHON="$LECTURE_HERMES_SOURCE/.venv/bin/python"
"$PYTHON" -m pip install -r examples/19-enterprise-dataset/requirements.txt
bash examples/19-enterprise-dataset/setup.sh
"$PYTHON" examples/19-enterprise-dataset/scripts/convert.py
cat examples/19-enterprise-dataset/rejected.jsonl
```

转换收入12题、排除4条；主路径预定只跑001、002、004、010四题，其余供扩展。仓库配置使用相对路径；运行器在本轮临时配置中填入当前解释器及引擎脚本的绝对路径，避免上游从题目工作区启动时找不到脚本，并保留配置副本。不读取个人绝对路径。

## 执行与隔离

显式设置 `GLM_API_KEY`、`GLM_BASE_URL`，每轮使用新目录：

```bash
export LECTURE19_OUTPUT_DIR="$PWD/examples/19-enterprise-dataset/output/my-new-run"
"$PYTHON" examples/19-enterprise-dataset/scripts/run_suite.py
"$PYTHON" examples/19-enterprise-dataset/scripts/analyze.py
# 可选本地网关补充演示，运行后可再次分析：
"$PYTHON" examples/19-enterprise-dataset/scripts/gateway_demo.py
```

运行器先在本轮目录重做评分器自测与隔离探针，再执行原生 validate 和两引擎 run。可单独运行 `scripts/selftest.py`，不调用模型。缺少已验证的隔离条件时停止；禁止裸进程降级。宿主禁止嵌套沙箱时，失败不能解释为隔离通过。

安装到被测环境的只有 Skill，任务目录只有 request/orders/ledger/channel 输入快照。评分材料在 grader-only；操作系统限制文件读取。探针验证工具越界、直接 open 答案/来源/其他题快照和输入写入均被拒绝。解释器与源码可读目录不得含答案副本。评分在执行结束后进行；另存目录并不足以隔离。

Hermes 使用原生预载入口；适配器读取四张快照，模型工具集为空，每题一次模型回答、最多4096输出Token。主路径4次回答；可选本地网关再加2次，上游传输退避可能增加HTTP请求。费用以供应商账单为准。教学小样本未证明生产非劣效，未连接真实 Langfuse，也没有真实采纳事件。

## 输出与题目

`protocol.json` 保存事先约定和版本哈希；已有该文件时拒绝覆盖。原生报告在 `skill-up/`，执行状态在 `native-run-status.json`，逐题输出、缺失结果和判断依据见 `paired-differences.json/.md`。`gateway_demo.py` 是本地稳定分组与反馈关联补充演示；分析按用例编号独立配对；如有本轮网关记录，再附接线检查。

观察题：为一道题保存输入快照、独立预期、Skill/服务/评分器版本、两版输出与判断依据，并沿 request_id 找回来源。指出答案、旧报告、历史采纳和截止后记录中哪些不能交给 Agent，并说明用途。思考题：为什么按旧采纳值评分，会使新版答错也保持同一个80%？完整答案另行发放。
