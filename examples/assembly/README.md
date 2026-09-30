# 自进化 Agent 总装工程

《自进化 Agent 工程实战》随课总装项目。它用真实组件搭出一个会持续工作、持续学习、并能跨实例共享改进的 Agent。所有环节都真实联网运行，不是模拟。

课程入口：https://time.geekbang.org/column/intro/101188801

## 最终架构（六部分）

1. **在线前台 Agent Loop**：真实 Hermes `AIAgent` 接收任务、调用工具、给出结论。
2. **持久化**：Memory（事实）、Skill（方法）、`state.db`（原始流水）。
3. **后台学习**：轮末异步复盘，best-effort，不阻塞前台。
4. **技能生命周期**：创建、增量修订、Curator 整合归档、快照恢复。
5. **评测与版本**：业务评测、GEPA 离线优化，ADOPT / REJECT / RESTORE。
6. **SkillClaw 跨实例共享**：多实例阅历上传、聚合演化、验证发布、第三实例加载。

递归飞轮：更高质量的 Skill 服务任务，产生更高质量的 rollout，轮末复盘再升级 Skill。

## 前置准备

```bash
# 在仓库根目录执行：拉取 Hermes / SkillClaw / OpenViking，建立 Hermes venv
bash scripts/setup_deps.sh

# 配置智谱 API 密钥（也可写入 ~/.hermes/.env）
export GLM_API_KEY="你的智谱开放平台密钥"
```

## 目录结构

```text
examples/assembly/
  run_assembly.py              # 总编排入口（按场景驱动真实组件）
  assembly/
    config.py                  # 统一配置：路径、模型、密钥（只从环境变量）
    contracts.py               # 共享数据契约（任务/评测/决策/共享）
    runtime/                   # 真实 Hermes 前台 + 原生异步后台
    skillclaw/                 # 真实 SkillClaw evolve_server 接入
    lifecycle/                 # 技能创建/Curator/快照
    eval/                      # 业务评测、评分器自检、采用决策
  scenarios/                   # 支付场景任务定义
  output/                      # 实跑证据（大文件/二进制不入库）
```

## 运行

```bash
# 冒烟：前台跑一个任务，观察后台复盘不阻塞
python run_assembly.py smoke

# 支付场景：同一正式目录连续处理多任务，触发技能创建/修订/评测
python run_assembly.py scenarios

# 跨实例共享：A/B 上传阅历，SkillClaw 演化发布，第三实例加载
python run_assembly.py shared

# 完整：六部分按场景依次运行并汇总证据
python run_assembly.py all
```

## 验收标准（证据写入 `output/`）

| 部分 | 证据 | 通过标准 |
|---|---|---|
| 前台 | `task_runs.json` | 真实工具调用与回执，给出业务结论 |
| 后台 | `background_reviews.json` | `blocked_foreground=false`；后台延迟/失败时下一前台任务照常 |
| 持久化 | `state.db`、Memory/Skill 目录 | 流水、事实、方法各自落盘 |
| 生命周期 | `candidates/`、`snapshots/` | 候选与正式目录隔离；快照可恢复 |
| 评测与版本 | `eval_reports.json`、`decisions.json` | 评分器自检通过才 ADOPT；REJECT/RESTORE 语义分清 |
| 共享 | `shared_revisions.json` | 发布经第三方验证；第三实例加载并在下一轮真实消费 |

## 边界与纪律

- 密钥只从环境变量读取，不写入代码、日志或证据文件。
- `.deps/` 是上游依赖，不纳入本仓库；如需固定版本见 `scripts/setup_deps.sh`。
- `output/` 中的二进制与数据库不入库，只保留可阅读的文本证据；读者重跑即可得到最新输出。
