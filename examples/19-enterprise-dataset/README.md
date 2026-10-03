# 企业数据集：把经历转为可重跑的题

[上一节](../18-business-eval/README.md) · [下一节](../20-gepa-offline-optimization/README.md)

## 问题与案例

第 18 章在脚本里放题，本章进一步从请求与业务快照转换 YAML。关键不是多收历史日志，而是明确哪些字段可交给 Agent、哪些是独立答案、哪些历史反馈仅作来源。payment-002 的受理未终结与 payment-004 的跨商户同号分别检验状态与对象边界；旧 accepted=1 不能当作新版答对的评分。

## 代码阅读路线

[scripts/convert.py](scripts/convert.py) 形成题目与 rejected.jsonl；[scripts/run_suite.py](scripts/run_suite.py) 建本轮配置、执行自测与原生 validate / run；[scripts/hermes_worker.py](scripts/hermes_worker.py) 用真实 Hermes 预载方法和只读快照；[scripts/grader.py](scripts/grader.py) 独立评分；[scripts/analyze.py](scripts/analyze.py) 按编号配对，而非按报告位置对齐。

[统一环境与模型准备](../../docs/MODEL_SETUP.md)

主练习整理教学导出、检查排除原因、生成原生 YAML，校验后使用原手写服务与真实 Hermes 两个自定义执行引擎，再按用例编号配对。快照恢复另见 `../19-snapshot-restore/`，是补充实验。

## 准备

当前入口使用 Python 3.12（dependencies.json 中 3.11 为历史观察元数据）、Go 1.25+ 和 macOS `sandbox-exec`。`dependencies.json` 固定 Skill Up 与 Hermes 提交；Go 依赖由固定提交的 go.mod/go.sum 锁定。补丁只使评分临时目录遵循 TMPDIR。Hermes 所选解释器须已装上游依赖。

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

显式设置 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`，每轮使用新目录：

```bash
export DEEPSEEK_BASE_URL=https://api.deepseek.com
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

## 把历史记录变成评测题要守住时间边界

任务输入应反映任务开始时可取得的资料，独立答案根据同一截止时点判断；后来的处理结果只能用于受控评分来源，不能混进模型当时的输入。历史 accepted 是当时的反馈事件，可能受流程或人工判断影响，不等于新版回答满足当前业务规则。

转换的排除清单使数据集不只保留方便成功的记录。request_id 用于来源关联，用例编号用于结果配对，两个引擎缺题也要单列。评分材料分目录后还需 OS 读取限制及探针，才能检查运行中的答案泄漏；协议记录与真实隔离结果应一起保存。

## 在学习循环中的作用

从经历生成独立题需要保留来源与排除原因，同时把模型可见输入和评分材料分开。本章形成可重跑的比较数据；接下来离线优化只能在约定数据上搜索，再按固定验证条件判断候选。

## 面试讲述与追问

我的关注点是经历转成题以后，输入、答案和旧反馈仍能分清。转换器固定任务截止时点，保留 request_id 与排除原因，评分材料由独立侧读取，再用 OS 探针验证运行中不能越界。相比直接回放整段历史，这样能防止后来的答案泄漏。题目可重跑是工程产出；两引擎效果和真实采纳必须依据各自记录，不能拿历史 accepted 值当新版分数。

**追问：把答案放在 grader-only 就能保证隔离吗？** 目录名只表达用途。还要检查解释器和可读源码中有没有答案副本，并验证工具、直接文件读取与符号链接边界；沙箱启动失败时停止，不能把失败解释为隔离成功。

讲述时可打开 [convert.py](scripts/convert.py)、[hermes_worker.py](scripts/hermes_worker.py)、[grader.py](scripts/grader.py) 核对实现或原始记录；个人贡献与结果的表述规则见 [项目面试指南](../../docs/INTERVIEW_GUIDE.md)。

## 工程应用与观察练习

数据集工程至少固定 request_id、事件时间、截止时间、来源、输入哈希、独立预期与排除原因。相同事件的不同文本不当作独立样本。OS 限制读取评分材料，不把“另存 grader-only 目录”当作隔离。原生 worker 使用 provider=custom 的 OpenAI 兼容路由，模型与官网地址仍为 DeepSeek。本章的 Python 3.11 是历史运行元数据，当前课程准备入口统一 3.12，当前平台覆盖见验证报告。

沿 payment-004 的 request_id 核对来源、截止时间、模型输入与 grader-only 预期，再查看 rejected.jsonl 中一条排除理由。解释把旧 accepted 值直接当评分，会如何掩盖新版的串户错误。
