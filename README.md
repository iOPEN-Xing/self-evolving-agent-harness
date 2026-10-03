# 自进化 Agent 实战 · 随讲实验

极客时间专栏 [《自进化 Agent 实战》](https://time.geekbang.org/column/intro/101188801) 的配套练习仓库。课程以 [Hermes](https://github.com/NousResearch/hermes-agent) 为主线 Agent 框架，配合 [SkillClaw](https://github.com/AMAP-ML/SkillClaw) 实现跨实例经验共享，模型统一使用智谱 **GLM-5.2**。

> 课程入口：https://time.geekbang.org/column/intro/101188801

## 工程分析与验证入口

先阅读 [架构与可信边界](docs/ARCHITECTURE.md)，再按 [运行和故障处置手册](docs/RUNBOOK.md) 选择实验路线。[工程复核记录](docs/ENGINEERING_REVIEW.md) 对照本轮修复、测试及未验证范围。

无需模型密钥的工程检查使用独立 Python 3.12 环境：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements-dev.lock.txt
make verify
make doctor
```

该入口检查源码、Notebook 单元格和离线回归；真实模型、Hermes/SkillClaw 安装与操作系统沙箱按各讲入口另行验收。开发锁文件不替代真实运行环境的依赖清单。

## 实验目录

### 第 01—09 讲：Codex SDK + Notebook

| 讲次 | 实验 | 你会观察到什么 |
| --- | --- | --- |
| 第 01 讲 | [从 Python 启动一次 500 告警调查](examples/01-agent-loop/) | 最小 Agent Loop：只给目标和边界，Agent 自己读告警、日志和代码定位根因，留下可核对的工具事件与调查报告 |
| 第 02 讲 | [一封迟到的信：改变一个正在工作的 Agent](examples/02-instruction-control/) | 巡检途中收到告警，用 `steer()` 让告警进入同一个 Turn、复用上下文；再用 `interrupt()` 对比转向与中断 |
| 第 03 讲 | [上下文引擎](examples/03-context-engine/) | 沿命令回执核对：文件可以被读取，与文件内容已进入某次模型调用，是两件事 |
| 第 04 讲 | [AGENTS.md](examples/04-agents-md/) | 规则写在哪里，决定哪些任务会使用它；旧 Thread 的临时纠正不会自动进入新 Thread |
| 第 05 讲 | [上下文压缩](examples/05-context-compression/) | 压缩缩小当前输入，却不改写任务目标、判断状态与授权；字符数不等于 token 数 |
| 第 06 讲 | [会话分支](examples/06-thread-fork/) | 两个分支继承背景、排除旧结论，A、B 的新观察互不串入彼此与父历史 |
| 第 07 讲 | [经验技能化](examples/07-skill-extraction/) | 从真实经历提炼可复用方法、去掉旧事故答案，新任务依据当前材料而非旧结论 |
| 第 08 讲 | [渐进披露](examples/08-progressive-disclosure/) | 每层资料在需要时才进入模型输入：描述、正文与参考资料分别在不同时刻被读到 |
| 第 09 讲 | [隔离与审批](examples/09-isolation-approval/) | 权限范围由执行环境与独立审批控制：先只读，再局部可写，固定报告命令被拒绝、一次性批准后放行 |

第 10 讲是背景与机制定位，没有独立练习。

### 第 11—23 讲及综合实践：脚本练习

以下练习已提供源码与运行入口；各讲 README 说明原生接口、自建教学循环及已有实跑结果。本次入口修复没有重新执行全部后半程模型练习，不能据此扩大其平台验证范围。

| 讲次 | 练习目录 | 推荐入口与边界 |
| --- | --- | --- |
| 11 | [后台复盘](examples/11-background-review/) | `bash examples/11-background-review/run.sh` |
| 12 | [Memory 修订](examples/12-memory-revision/) | `bash examples/12-memory-revision/run.sh` |
| 13 | [旧会话回查](examples/13-session-lookup/) | `bash examples/13-session-lookup/run.sh` |
| 14 | [外部记忆](examples/14-external-memory-provider/) | `bash examples/14-external-memory-provider/run.sh` |
| 15 | [自主技能学习](examples/15-skill-autocreation/) | `bash examples/15-skill-autocreation/run.sh` |
| 16 | [增量技能演化](examples/16-skill-incremental-patch/) | `bash examples/16-skill-incremental-patch/run.sh` |
| 17 | [技能生命周期与整合](examples/17-curator/) | `bash examples/17-curator/run.sh lifecycle`；`consolidate` 整合练习仅 macOS 验证 |
| 18 | [业务评测](examples/18-business-eval/) | `bash examples/18-business-eval/run.sh` |
| 19 | [企业数据集](examples/19-enterprise-dataset/) | 两引擎原生 YAML 运行与逐题配对，见本讲说明 |
| 20 | [离线优化](examples/20-gepa-offline-optimization/) | 官方 GEPA + Hermes：`run_exercise.py all`，解释器及依赖见本讲说明 |
| 21 | [会话收集](examples/21-skillclaw-session-collection/) | Hermes venv 执行 `run21.py`；原生采集与隔离候选 |
| 22 | [跨实例修订](examples/22-skillclaw-shared-revision/) | Hermes venv 执行 `run22.py`；原生读取与回退复测 |
| 23 | [总装参考实现](examples/23-final-assembly/) | Hermes venv 执行 `final_assembly.py` |
| 综合实践 | [端到端练习](examples/capstone/) | Hermes venv 执行 `run_assembly_light.py capstone`；新建运行编号 |
| 扩展 | [原生组件总装工程](examples/assembly/) | Hermes venv 执行 `run_assembly.py smoke`；其他场景见目录说明 |

## 环境要求

- macOS 或 Linux（Windows 请使用 WSL2）、Bash、Git。第 17 讲 `consolidate` 依赖 macOS `sandbox-exec`，其他平台入口会退出；不可视作已支持 Linux/WSL2。第 03、08、09 讲还要求宿主允许 Codex 原生 Sandbox。
- 本仓库统一使用 Python **3.12**；第 01—09 讲按此版本验证，Notebook 会检查内核版本与固定依赖接口。
- 第 11–23 讲及综合实践建议安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)：准备脚本用它创建 Python 3.12 环境；没有 uv 时，需自行安装 Python 3.12 和 venv/pip。后半程已有实跑记录的解释器版本以原记录为准，本次没有据安装版本统一而重写历史结果。
- 智谱 API Key（GLM-5.2），从环境变量 `GLM_API_KEY` 读取，兼容 `BIGMODEL_API_KEY`。不要把真实 key 写入脚本或配置文件。

### 第 01—09 讲（Codex SDK + Notebook）

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r examples/requirements-notebooks.txt jupyterlab
export BIGMODEL_API_KEY="你的智谱 API Key"  # 01、02 读取此变量
export GLM_API_KEY="$BIGMODEL_API_KEY"       # 03—09 读取此变量
```

### 第 11–23 讲及综合实践：一键准备上游依赖

在本仓库根目录运行：

```bash
bash scripts/setup_deps.sh
```

脚本获取下列四个上游仓库，并在 `.deps/hermes-agent/.venv` 创建环境，按上游的 `pyproject.toml` 安装 Hermes 和 SkillClaw 服务端依赖。自进化扩展与 OpenViking 在本仓库中作为源码参考获取，不在此启动服务。`.deps/` 已被 Git 忽略，不随本仓库分发。

| 目录 | 上游来源 |
| --- | --- |
| `.deps/hermes-agent/` | [Hermes 主框架](https://github.com/NousResearch/hermes-agent) |
| `.deps/hermes-agent-self-evolution/` | [Hermes 自进化扩展](https://github.com/NousResearch/hermes-agent-self-evolution) |
| `.deps/SkillClaw/` | [SkillClaw 跨实例共享](https://github.com/AMAP-ML/SkillClaw) |
| `.deps/OpenViking/` | [OpenViking 上下文库](https://github.com/volcengine/OpenViking) |

clone 和依赖安装**默认直连**，不依赖本机 Clash。需要代理时，显式指定自己的代理地址（下面端口仅为示例）：

```bash
PROXY=http://127.0.0.1:7890 bash scripts/setup_deps.sh
# 显式直连（与默认相同）：
PROXY= bash scripts/setup_deps.sh
```

脚本可重复运行：已有源码目录不重复 clone，也不自动 pull；已有 Python 3.12 venv 继续使用；其他版本会报错，不会被脚本删除或覆盖。旧环境如需更换，请先自行备份并重建。重新执行依赖安装可补齐中断的安装。需要更新某个上游时，可自行运行 `git -C .deps/hermes-agent pull`（其他目录同理），再运行准备脚本。首次获取的是上游默认分支，后续上游接口变化可能影响练习。

也可以手动获取源码，再由同一脚本完成环境安装。以下命令从仓库根目录运行，只 clone 尚不存在的目录：

```bash
(
  # 手动 clone 同样默认直连；需要代理时，先 export PROXY=你的代理地址。
  PROXY="${PROXY-}"
  export http_proxy="$PROXY" https_proxy="$PROXY" HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY"
  unset all_proxy ALL_PROXY
  mkdir -p .deps
  [ -d .deps/hermes-agent ] || git -c http.proxy="$PROXY" clone https://github.com/NousResearch/hermes-agent.git .deps/hermes-agent
  [ -d .deps/hermes-agent-self-evolution ] || git -c http.proxy="$PROXY" clone https://github.com/NousResearch/hermes-agent-self-evolution.git .deps/hermes-agent-self-evolution
  [ -d .deps/SkillClaw ] || git -c http.proxy="$PROXY" clone https://github.com/AMAP-ML/SkillClaw.git .deps/SkillClaw
  [ -d .deps/OpenViking ] || git -c http.proxy="$PROXY" clone https://github.com/volcengine/OpenViking.git .deps/OpenViking
)
bash scripts/setup_deps.sh
```

> 第 01–02 讲的 Codex SDK＋LiteLLM 适配器读取 `BIGMODEL_API_KEY`；第 03—09 讲仅读取 `GLM_API_KEY`，第 11 讲以后的脚本以 `GLM_API_KEY` 为主，兼容 `BIGMODEL_API_KEY`。准备脚本不需要模型 key；运行模型时保持直连。

## 运行实验

### 第 01—09 讲（Notebook）

```bash
# 从仓库根目录直接打开已提供的 Notebook，无需生成脚本。
python -m jupyterlab examples/01-agent-loop/workshop.ipynb
# 第 02 讲：python -m jupyterlab examples/02-instruction-control/workshop.ipynb
# 第 03—09 讲：python -m jupyterlab examples/
```

### 第 11–23 讲及综合实践（Python 脚本）

从仓库根目录开始。先准备依赖，再设置模型环境变量并运行练习；已有 `run.sh` 的目录会自动选择 Hermes venv 并清除代理：

```bash
bash scripts/setup_deps.sh
unset http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
export GLM_API_KEY="你的智谱 API Key"
export GLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4"

bash examples/14-external-memory-provider/run.sh
# 也可以进入练习目录后执行 bash run.sh。

# 17 的两个入口：
bash examples/17-curator/run.sh lifecycle
bash examples/17-curator/run.sh consolidate  # 仅 macOS
examples/20-gepa-offline-optimization/.venv/bin/python examples/20-gepa-offline-optimization/run_exercise.py all

# 19、23、综合实践没有 run.sh，直接使用 Hermes venv：
.deps/hermes-agent/.venv/bin/python examples/19-enterprise-dataset/scripts/run_suite.py
.deps/hermes-agent/.venv/bin/python examples/23-final-assembly/final_assembly.py
.deps/hermes-agent/.venv/bin/python examples/capstone/run_assembly_light.py capstone
```

练习默认从脚本自身位置定位 `.deps/hermes-agent`，不依赖启动目录。如需复用别处的 Hermes 源码，可设置 `HERMES_SRC` 为其绝对目录，且该目录下须有已安装依赖的 `.venv`；直接运行 Python 时也应使用 `"$HERMES_SRC/.venv/bin/python"`。各练习的步骤与前置条件见对应 `README.md`，第 22 讲主练习会独立生成本轮来源会话；旧补充循环的前置条件另见本讲说明。

练习使用独立的运行目录。文件位置分为三类：

| 类型 | 位置与用途 |
| --- | --- |
| 示例输入 | 告警、日志、服务器信息及业务案例均为构造的教学材料；有些随脚本生成，有些以示例文件或代码常量保存在仓库中 |
| 运行时目录 | 01、02 在系统临时目录创建输入、Codex 配置和日志；03—09 在各讲 `.runtime/<运行编号>/` 下创建，Git 忽略；后半程独立 `HERMES_HOME` 位于 `.hermes-home/`、`output/` 或系统临时目录，以各讲 README 为准 |
| 保存输出 | 01、02 报告保留在本次临时目录；03 至 09 的请求、会话记录与工作文件保存在各讲 `.runtime/<运行编号>/` 中，单元格输出可随 Notebook 手动保存，分享前需自行脱敏；后半程通常写入各讲 `output/`。部分示例结果随仓库保存，不代表读者本次运行成功 |

关闭连接不会自动删除所有运行目录。重跑、覆盖或清理规则见各讲 README；分享前清除 Notebook 输出，并检查日志中是否含敏感信息。

## 说明

- 涉及模型的练习会**真实调用 GLM-5.2 付费接口**，运行前请确认账号额度；Agent 的具体动作可能因模型版本而略有差异，属于正常现象。
- API Key 从环境变量读取；01、02 在缺失时可隐藏输入。凭证不会写入脚本或配置文件，分享运行输出前仍须检查。
- 使用 Hermes 的练习使用独立的 `HERMES_HOME`；第 18 讲仅比较两套指令；需要保留本次记录时，先保存相应目录。
- 教学材料、运行状态和保存结果的位置不同，不能笼统理解为“全部生成于临时目录、不写入仓库”。
- 四个上游项目源码均放在 `.deps/` 目录（已 gitignore），作为上游依赖引用，不随本仓库分发。

## 许可证

[MIT](LICENSE)
