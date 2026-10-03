# 环境与模型：先验证连接，再运行课程

本课程统一使用 `deepseek-flash`，官网地址为 `https://api.deepseek.com`。模型名和协议依据 [DeepSeek 官方接口说明](https://api-docs.deepseek.com/)。密钥只从当前进程的 `DEEPSEEK_API_KEY` 读取；旧版 GLM / BIGMODEL 凭证不再用于课程调用。

## 1. 先完成无需模型的检查

准备环境时区分三种可复现性：源码清单固定实现，依赖锁固定相应安装集合，真实请求记录固定实验条件。源码相同而依赖不同仍可能出现接口差异；探针相同而业务输入不同也不能合并评分。开发环境、Notebook 和 Hermes 分开准备，能减少课程之间的依赖干扰。

从仓库根目录执行，使用 Python 3.12：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements-dev.lock.txt
make verify
```

检查包含 Python、Notebook 代码语法、数据格式、shell 语法和离线回归。Notebook 会转换 IPython 语法后编译，但不会调用模型或执行工具；通过静态检查不能替代每章实验。

## 2. 只在运行环境提供凭证

macOS / Linux 终端可用隐藏输入，避免将密钥写入命令历史：

```bash
read -r -s DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
```

Jupyter 进程继承启动终端的环境；未设置时各 Notebook 通过 `getpass` 提示。不要把凭证填进代码单元。运行前检查 `DEEPSEEK_API_KEY` 是否非空即可，不打印内容。

调用模型的课程脚本清除大小写 HTTP / HTTPS / ALL 代理变量。`urllib` 使用 `ProxyHandler({})`，OpenAI 回放客户端使用 `trust_env=False` 或清除环境代理。获取源码与安装包是另一个步骤，可为准备脚本指定 `PROXY`。

## 3. 01–09 章：Codex SDK 与原生 Responses

```bash
uv pip install --python .venv/bin/python -r examples/requirements-notebooks.txt jupyterlab
.venv/bin/python -m ipykernel install --user --name harness-course --display-name 'Harness Python 3.12'
.venv/bin/python -m jupyterlab examples/01-agent-loop/workshop.ipynb
```

选择 `Harness Python 3.12`，从公共准备格开始按序运行。SDK 固定为 `openai-codex==0.154.0`。公共准备可补装 SDK；若内核已导入不同版本，安装后重启内核。

`Lab` 启动课程专用 App Server 与本机观察服务：Codex 使用随机本地令牌，观察服务通过 TLS 将 Responses 请求原样转发到官网 `/responses`。真实密钥留在观察服务进程内，工具子进程的凭证变量为空。每次运行有独立 `CODEX_HOME`，不会修改个人 Codex 配置。实现见 [notebook_support.py](../examples/notebook_support.py)。

采用原生 Responses 是因为课程需要检查上下文输入、`steer()` 与外部工具回执；不再使用 LiteLLM 将它们转换成 Chat Completions。这与 [DeepSeek 的 Codex 接入协议](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/) 一致。

## 4. 11–23 章：Hermes 与共享组件

```bash
bash scripts/setup_deps.sh
.deps/hermes-agent/.venv/bin/python scripts/model_probe.py
```

准备脚本读取 [依赖版本清单](../deps.lock.json)，获取四个上游源码并准备共用解释器。已有目录版本不符时报告差异，不覆盖本地修改。可用 `HERMES_SRC` 指向额外的实验 checkout，但报告需记下其提交；不能把不同版本的结果当作同一实现。

Hermes 使用 `provider="deepseek"`、`model="deepseek-flash"` 和官网地址；只使用核心依赖与 SkillClaw 服务扩展。各章是否真正创建 `AIAgent`、调用原生 skill-up / GEPA，或使用自建工具循环，见章节自己的“代码与证据”说明。

自建 Chat Completions 循环显式发送 `thinking: {type: disabled}`，避免只保存 `content` 却漏传思考模式要求的 `reasoning_content`。原生 Responses 保留 reasoning 项。协议依据 [思考模式](https://api-docs.deepseek.com/guides/thinking_mode/) 与 [工具调用](https://api-docs.deepseek.com/guides/tool_calls/)；更换思考模式属于执行协议变更，需重新比较所有版本。

## 5. 连接与实验故障分开定位

| 现象 | 先检查 | 此时能作出的结论 |
|---|---|---|
| 401 | 当前进程是否提供正确凭证 | 认证未通过；没有完成模型实验 |
| 402 / 429 | 官网账单、配额与返回错误 | 额度或限流问题；不要把空回答记为业务失败样本 |
| 400 | 模型名、协议、工具回执配对、thinking 参数 | 请求协议不合法；先保留脱敏错误再修复 |
| TLS / 超时 | 官网可达性、时限、代理清理 | 调用未完成；不代表模型判断错误 |
| Turn failed / interrupted | 本次请求与 Turn 记录 | 请求未完成；中断不自动回滚已执行动作 |
| Sandbox 启动失败 | 操作系统与执行器日志 | 隔离环境没启动；不能算“越界写被拒绝” |
| 请求完成但评分失败 | 原始回答、独立 expected 与评分明细 | 可以作为该执行协议下的业务反例 |

`model_probe.py` 只进行短 Chat / Responses 连接检查，记录请求模型、返回模型、usage、耗时和状态，不保存认证头。通过它只能证明本次接口可用，不能证明整套 Agent 或业务流程有效。

## 6. 正确解读历史材料

版本列表的名字带 lock，不一定是完整安装锁。`requirements-dev.lock.txt` 包含解析后的依赖与哈希；第 20 章优化器文件目前只固定直接 `gepa` 版本；Hermes 观察列表来自特定历史环境。后两类需在目标平台保存实际解析结果，不能据文件名宣称全部传递依赖已固定。

仓库里已有的 `output/`、`output_timing.txt`、旧报告和已执行 Notebook 是历史教学记录。出现 GLM 标识时保持其原始来源；不要把旧记录改名后称为 DeepSeek 验证。新实验使用本次运行目录、版本、输入哈希和请求协议。当前迁移核验汇总单独记录在 [文档验证报告](DOCUMENTATION_VALIDATION.md)。

## 面试讲述与追问

环境工程的目的，是让同一个实现能在明确条件下复现。我分别固定源码、开发依赖和执行协议，先做离线检查，再用短探针定位认证或协议问题，最后进入业务实验。Notebook 保留原生 Responses，自建 Chat 循环明确 thinking 设置；更换协议会改变比较条件。连接 HTTP 200 是接口证据，不能替代整套工具、学习与业务验收。

**追问：源码提交固定了，为什么还可能跑不起来？** 安装集合、解释器、系统沙箱和协议都可能不同。完整哈希锁、直接版本固定与历史安装观察的力度不同，应保存目标环境清单，并核对错误发生在哪一层。

讲述时可打开 [deps.lock.json](../deps.lock.json)、[setup_deps.sh](../scripts/setup_deps.sh)、[model_probe.py](../scripts/model_probe.py) 核对实现或原始记录；个人贡献与结果的表述规则见 [项目面试指南](INTERVIEW_GUIDE.md)。
