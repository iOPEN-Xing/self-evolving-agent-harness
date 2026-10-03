# 自进化 Agent Harness：从执行到可验证的改进

本仓库整理 [《自进化 Agent 实战》](https://time.geekbang.org/column/intro/101188801) 的随课实验。前半程用 Codex SDK 观察执行与上下文，后半程用 Hermes、SkillClaw、业务 Eval 与官方 GEPA 拆解经验学习。模型统一为 **deepseek-flash**，通过 DeepSeek 官网接口调用。

课程的目标是让每个“成功”都有对应证据：工具是否真的执行、记忆是否落盘、候选是否经过独立评测、接收实例是否真的加载、失败后能否恢复。构造的支付与巡检材料便于重跑，不代表已经验证生产收益。

这里的自进化发生在可复用方法上：执行留下经历，模型据此产生 Skill 候选，控制器按固定业务条件决定是否采用，下一次任务实际加载所选版本。一次运行完成、候选被采用和业务结果提升分别核对。想先看清这条主线，可读 [自进化如何发生](docs/SELF_EVOLUTION.md)，沿一笔付款追到候选、成对评分、版本切换与后续执行。

## 先从这里开始

1. 阅读 [环境与模型准备](docs/MODEL_SETUP.md)，先做无需密钥的 `make verify`。
2. 从 [第 01 章](examples/01-agent-loop/README.md) 开始，按 Notebook 公共准备格依次执行。
3. 通过 [第 10 章机制桥接](docs/10-learning-loop.md) 区分 Session、Memory、Skill，再进入后半程。
4. 按 [课程与代码对照](COURSE_MAP.md) 找具体入口；想直接查看完整工程，阅读 [Assembly](examples/assembly/README.md) 和 [综合实践](examples/capstone/README.md)。

每章文档先说明问题与案例，再进入代码、运行证据、失败分支和工程应用。Notebook 用稳定单元 ID 定位，脚本文档列出实际函数与产物。当前核验范围见 [文档与模型验证报告](docs/DOCUMENTATION_VALIDATION.md)。

## 学习路线

学习这套课程时，先把“判断能力”与“运行步骤”连起来：你应能说明一次结论依赖什么事实、一次更新改变什么内容、一次验收排除了什么反例。只会启动脚本还无法判断结果可信；只记住自进化名词也无法定位失败在哪个模块。

每个机制都有保留条件。历史记录不能代替当前工具查询，摘要不能代替原文，候选不能代替正式版，版本号不能代替本任务实际加载的内容。把这些关系核对清楚，才适合将教学机制接到新的业务工具。

| 阶段 | 章节 | 核心问题 | 案例推进 |
|---|---|---|---|
| 执行可观察 | 01–03 | 如何行动、转向、将材料装入请求 | 支付 500 → 执行中告警 → 部署记录读取 |
| 长任务可控制 | 04–09 | 规则、压缩、分支、技能、加载与权限 | 因果纠正 → 状态摘要 → 新事故 → 目录边界 |
| 经验可维护 | 10–17 | 保存什么，怎样修订与整理 | 缓存版本纠错 → 会话回查 → 共享事实 → 支付方法更新 |
| 改进可验证 | 18–20 | 候选是否改善业务，怎样避免评分与数据泄漏 | 分次付款/串户 → 事件转题 → 官方 GEPA 与独立留出 |
| 版本可分发 | 21–23 | 采集、发布、加载与回滚如何分开验证 | 超时诊断 → 连接池分支 → 受控巡检状态机 |
| 综合实践 | Capstone / Assembly | 真实组件怎样接成一轮可审计流程 | 原生 Hermes 支付查询、后台修订、候选隔离与采用 |

## 章节入口

| 章节 | 文档 | 主要执行入口 |
|---|---|---|
| 01 | [Agent Loop](examples/01-agent-loop/README.md) | `workshop.ipynb` |
| 02 | [执行中控制](examples/02-instruction-control/README.md) | `workshop.ipynb` |
| 03 | [上下文装配](examples/03-context-engine/README.md) | `workshop.ipynb` |
| 04 | [项目规则](examples/04-agents-md/README.md) | `workshop.ipynb` |
| 05 | [上下文压缩](examples/05-context-compression/README.md) | `workshop.ipynb` |
| 06 | [会话分支](examples/06-thread-fork/README.md) | `workshop.ipynb` |
| 07 | [经验技能化](examples/07-skill-extraction/README.md) | `workshop.ipynb` |
| 08 | [渐进披露](examples/08-progressive-disclosure/README.md) | `workshop.ipynb` |
| 09 | [隔离与审批](examples/09-isolation-approval/README.md) | `workshop.ipynb` |
| 10 | [执行与学习桥接](docs/10-learning-loop.md) | 机制文档，无独立程序 |
| 11 | [后台复盘](examples/11-background-review/README.md) | `run.sh` |
| 12 | [Memory 修订](examples/12-memory-revision/README.md) | `run.sh` |
| 13 | [Session 回查](examples/13-session-lookup/README.md) | `run.sh` |
| 14 | [外部 Memory Provider](examples/14-external-memory-provider/README.md) | `run.sh`；补充 `run_shared_sqlite.py` |
| 15 | [Skill 创建](examples/15-skill-autocreation/README.md) | `run.sh` |
| 16 | [Skill 增量修订](examples/16-skill-incremental-patch/README.md) | `run.sh` |
| 17 | [Curator](examples/17-curator/README.md) | `run.sh lifecycle / consolidate` |
| 18 | [业务 Eval](examples/18-business-eval/README.md) | `run.sh` |
| 19 | [企业数据集](examples/19-enterprise-dataset/README.md) | `scripts/run_suite.py` |
| 20 | [官方 GEPA](examples/20-gepa-offline-optimization/README.md) | `run_exercise.py all` |
| 21 | [会话采集](examples/21-skillclaw-session-collection/README.md) | `run21.py` |
| 22 | [跨实例共享](examples/22-skillclaw-shared-revision/README.md) | `run22.py` |
| 23 | [受控巡检参考实现](examples/23-final-assembly/README.md) | `final_assembly.py` |
| 综合 | [原生支付学习循环](examples/capstone/README.md) | `run_assembly_light.py capstone` |
| 扩展 | [模块化总装](examples/assembly/README.md) | `run_assembly.py smoke` |
| 附录 | [快照与方法回退](examples/19-snapshot-restore/README.md) | `snapshot_restore.py` |

脚本命令均从仓库根目录运行。后半程共用解释器为 `.deps/hermes-agent/.venv/bin/python`；第 20 章优化器使用本章独立环境。目录中的旧示例与主入口不同，各章明确标明身份，不混合其运行结果。

## 两套环境与一套模型

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements-dev.lock.txt
make verify
make doctor
```

离线环境安装编译与回归所需依赖，不调用模型。Notebook 运行依赖和后半程上游环境另见 [准备步骤](docs/MODEL_SETUP.md)。使用密钥前，在当前终端隐藏输入：

```bash
read -r -s DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
```

源码版本由 [deps.lock.json](deps.lock.json) 固定，准备入口为 [setup_deps.sh](scripts/setup_deps.sh)。已有目录提交不匹配时停止，不覆盖本地修改；获取源码可配置 PROXY 或 SOURCE_PROTOCOL=ssh。真实模型请求使用官网直连，不需要修改个人 Codex 或 Hermes 配置。

基础环境支持 macOS / Linux 的 Python 3.12。第 17 章 consolidate 与第 19 章执行隔离依赖 macOS sandbox-exec，其他平台不会降级为裸执行。CI 的跨平台离线通过不替代 OS 沙箱或生产部署验证。

## 工程文档

- [项目面试指南](docs/INTERVIEW_GUIDE.md)：Romain 项目讲述、个人贡献、设计取舍与证据追问。
- [架构与可信边界](docs/ARCHITECTURE.md)：原生组件、自建循环、候选与发布之间的责任。
- [运行与故障处置](docs/RUNBOOK.md)：证据核对、时限、失败记录与恢复。
- [工程复核记录](docs/ENGINEERING_REVIEW.md)：上一轮修复与离线测试范围。
- [迁移整理计划](docs/DOCUMENTATION_PLAN.md)：历史迁移范围及验收条件。
- [迁移验证记录](docs/DOCUMENTATION_VALIDATION.md)：真实 DeepSeek 执行、失败案例与未覆盖路径。
- [逐份文档复核](docs/DOCUMENTATION_AUDIT.md)：当前内容、源码依据和历史材料的解释范围。

输出与 .deps 默认留在本机；历史 GLM 记录保持原始来源。学习资料来源、MIT 许可证和上游归属保留。本仓库中的演练不自动具备跨主机一致性、生产鉴权、分布式发布事务或统计意义上的业务提升证明。

## 面试讲述与追问

我基于这套开源课程做了工程化整理，目标是让 Agent 的经验更新可解释、可验证、失败可恢复。系统把真实工具经历交给后台生成 Skill 候选，再由固定业务评分与版本策略决定是否采用，后续任务核对实际加载。我的整理重点是故障边界、来源绑定、文档与代码对应以及统一验收。当前能明确解释 Skill 层的一轮受控更新；已有四题对照 4/4 → 4/4 没有测得增益，生产效果与持续第二轮仍需验证。

**追问：项目最有工程价值的部分是什么？** 能把错误定位到执行、候选、评分、采用或加载阶段，并阻止缺失证据进入发布。例如快照先隔离导入、评分缺失拒绝采用、任务加载绑定内容哈希。价值应以具体回归和运行记录说明，不能只数模型或框架数量。

讲述时可打开 [ENGINEERING_REVIEW.md](docs/ENGINEERING_REVIEW.md)、[SELF_EVOLUTION.md](docs/SELF_EVOLUTION.md)、[verify.py](scripts/verify.py) 核对实现或原始记录；个人贡献与结果的表述规则见 [项目面试指南](docs/INTERVIEW_GUIDE.md)。
