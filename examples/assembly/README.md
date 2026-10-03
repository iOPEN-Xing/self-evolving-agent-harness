# 模块化 Assembly：按证据把真实组件接起来

前置：[第 23 章参考实现](../23-final-assembly/README.md) · 主实践：[原生支付 Capstone](../capstone/README.md)

本目录提供可复用工程模块。在线前台使用原生 Hermes AIAgent，SkillClaw 管理会话共享和演化，课程外层负责候选目录、评测协议、采用策略和恢复。配置存在、组件可导入与整轮成功是不同状态；当前运行覆盖以 [验证报告](../../docs/DOCUMENTATION_VALIDATION.md) 为准。

## 1. 一笔付款怎样走过系统

先按商户和订单查询教学流水，区分成功、处理中、渠道受理和商户处理。前台保存 Session，轮末原生后台可能提出可复用方法。方法先进入隔离候选，以相同数据、执行协议和独立 expected 比较基线与候选；只有满足策略才切换正式目录。接收实例拉取并核对本任务固定版本，回执证明实际使用。

这个流程的每条边都有检查，后台产生文件并不直接授权采用。请求受理不表示到账，Skill 下载不表示已加载，文件恢复也不恢复外部付款。

## 2. 代码阅读路线

| 路径 | 职责 | 关键边界 |
|---|---|---|
| [run_assembly.py](run_assembly.py) | 编排阶段与续跑记录 | 失败尝试保留，不按分数补跑 |
| [assembly/config.py](assembly/config.py) | 路径、DeepSeek、服务地址与环境凭证 | 不隐式加载个人密钥文件 |
| [assembly/contracts.py](assembly/contracts.py) | 任务、评测、候选和哈希契约 | 字段与真实文件对应 |
| [assembly/runtime/foreground.py](assembly/runtime/foreground.py) | 注册支付工具并创建原生 Agent | 只读教学数据，独立实例 home |
| [assembly/runtime/background.py](assembly/runtime/background.py) | 观察原生后台与前台关系 | 线程终止、工具成功、文件变化分开 |
| [assembly/lifecycle/skills.py](assembly/lifecycle/skills.py) | 候选、Curator、方法包快照 | 候选与正式目录隔离，失败保留证据 |
| [assembly/eval/runner.py](assembly/eval/runner.py) | 独立客户端的真实模型评测 | 冻结 Skill 全树、输入和协议 |
| [assembly/eval/judge.py](assembly/eval/judge.py) | 确定性付款评分 | 中文与结构字段一致，反例自检 |
| [assembly/skillclaw/client.py](assembly/skillclaw/client.py) | 原生共享、上传与发布接入 | 生成候选、验证发布、加载分别记录 |
| [assembly/skillclaw/validation.py](assembly/skillclaw/validation.py) | 独立进程回放与截止 | chat / PRM 预算、错误和重试不伪装通过 |
| [assembly/gepa.py](assembly/gepa.py) | 另一个自建离线教学模块 | 官方 GEPA 主课程在第 20 章 |
| [scenarios/orders.json](scenarios/orders.json) | 支付快照 | 构造数据，未连接支付系统 |

run_assembly 的 worker 在新进程先设 HERMES_HOME，再导入 Hermes，避免缓存全局路径串入其他实例。已加载正文与哈希另外记录，不能只根据磁盘目录宣布模型已采用方法。

## 3. 运行与依赖

先完成 [统一环境准备](../../docs/MODEL_SETUP.md) 与模型探针，在仓库根目录运行：

```bash
.deps/hermes-agent/.venv/bin/python -B examples/assembly/run_assembly.py smoke
.deps/hermes-agent/.venv/bin/python -B examples/assembly/run_assembly.py all
```

smoke 只观察一段原生前后台关系；all 执行编排中全部阶段。--run-id 继续指定运行，仍需遵守入口对阶段和失败尝试的检查。各独立演示入口 run_runtime_demo.py、run_lifecycle_demo.py、run_eval_demo.py、run_skillclaw_demo.py 用于拆开观察，不把其结果拼成一次全流程成功。

模型为 deepseek-flash / 官网地址。候选验证进程明确读取 DEEPSEEK_API_KEY，凭证不写入报告；命令无需 source 个人 .env。真实调用产生的用量以原始响应和供应商账单核对，阶段任务次数不等于 HTTP 请求次数。

## 4. 证据应怎样连接

先读本轮计划和运行状态，再按任务编号找 Session、工具回执与候选来源。评测报告需要完整 case 集、Skill 全树哈希、模型与执行参数、评分器版本和逐题原始输出。采用决定引用同一份冻结证据；共享端再给出发布版本、下载哈希、本任务固定哈希与实际读取回执。

快照恢复先在同级临时目录核对完整文件集合与哈希，再切换目录；失败保留原目录及可恢复备份。Curator 使用受控写入沙箱。POSIX 进程组取消用于作业时限，不能代替读写隔离或分布式事务。

## 在学习循环中的作用

模块化总装提供可复用的执行、候选、评分和版本控制边界。基础采用策略要求完整可信且不劣，Capstone 在其上增加严格增益；重用模块时先选择业务标准，再确认加载端实际使用的版本与原始回执。完整阶段与条件见 [自进化主线](../../docs/SELF_EVOLUTION.md)。

## 5. 落地顺序

第一步只接一项读取工具，固定对象、时间窗口和输出协议；第二步建立现有方法、独立 expected 和已知错误样本；第三步影子执行学习端，所有产物先是候选；第四步冻结评测并分权发布；第五步检验接收端加载、降级缓存、失败补偿与恢复。

本机 local SkillHub 演练没有证明跨主机一致性、租户鉴权或发布事务。未验证的 Curator、GEPA、Memory 使用和跨实例再学习能力不能因模块存在而标记通过。

## 观察练习

把候选中的支持文件也纳入哈希，再尝试只更新 SKILL.md 不更新 reference，检查评测与发布能否发现方法包不一致。模拟下载完成但任务仍用旧目录，比较共享哈希和任务哈希；若只看版本号，这个错误会被漏掉。
