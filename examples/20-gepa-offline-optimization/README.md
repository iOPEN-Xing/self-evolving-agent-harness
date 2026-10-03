# 离线优化：提案、搜索与采用是三个决定

[上一节](../19-enterprise-dataset/README.md) · [下一节](../21-skillclaw-session-collection/README.md)

## 问题与案例

有了题与评分，才能比较候选。训练、验证、独立留出各 3 题，分别包含已到账、仅受理、跨商户同号。训练已到账记录有 settlement_evidence；仅 ACCEPTED 不代表支付完成；跨商户同号不能读取首条就给答案。训练用于产生反馈，验证用于搜索，留出只在候选固定后使用。

## 代码阅读路线

[run_exercise.py](run_exercise.py) 的 `Harness.execute` 执行真实 Hermes，`Adapter` 提供评分轨迹，`reflection_lm` 发起官网反思请求，`static_check` 控制候选结构与长度，`adoption` 独立判断是否采用。搜索调用官方 gepa.optimize；[hermes_worker.py](hermes_worker.py) 预载方法并只开放 query_payment。旧 [gepa_offline.py](gepa_offline.py) 是自建原型，不等于官方搜索。

[统一环境与模型准备](../../docs/MODEL_SETUP.md)

本练习只保留一条判断链：GEPA 提出短候选，经真实 Hermes 运行，再看独立留出是否达到最小收益。没有赢家、验证没有改善、坏候选被拒绝，都是应当留下的结果。业务记录是课程构造材料；模型请求、Hermes 工具调用和返回内容均真实执行，模型与反思模型都使用 `deepseek-flash`。

本次精简版使用 `data/demo-v2-20260926/`：训练3题、验证3题、独立留出3题，各含已到账、仅受理、跨商户同号；另用1道公开跨商户题检验固定坏候选。旧版数据和旧版留出使用记录保留。新留出改用了新的业务背景，不能据此宣称生产泛化能力。

## 运行

从仓库根目录执行。现有优化器环境为 Python 3.12、`gepa==0.0.27`；Hermes 使用只读 `.deps/hermes-agent` 及其既有虚拟环境。

```bash
# 只核对预载、工具集合、评分和采用规则，不调用模型。
examples/20-gepa-offline-optimization/.venv/bin/python \
  examples/20-gepa-offline-optimization/run_exercise.py verify

# 显式把已有凭据加载到环境；不要把 key 写进代码或命令参数。
# 先按 MODEL_SETUP 在当前终端设置 DEEPSEEK_API_KEY
examples/20-gepa-offline-optimization/.venv/bin/python -u \
  examples/20-gepa-offline-optimization/run_exercise.py all
```

第20讲的 worker 与反思请求固定使用 `https://api.deepseek.com`；`DEEPSEEK_BASE_URL` 不会改变它们的端点。脚本只从显式环境变量 `DEEPSEEK_API_KEY` 读取密钥，worker 不读取用户或项目 `.env`。官网模型请求直连；入口移除代理环境变量，反思客户端使用 ProxyHandler({})。缺少密钥或遇到执行故障会留下记录并退出；不修模型答案，也不为业务答错重跑题目。

`all` 包含预载自测、搜索、完整候选验证、独立留出和公开故障题。`run` 只运行正常路径，`fault` 只跑公开故障题，`prepare` 生成本版本教学数据。输出统一放在 `examples/20-gepa-offline-optimization/output/native-gepa/<运行编号>/`，每次使用新目录。`latest.json` 指向最近一次命令，未必是最近一次完整联网运行。

同一份留出只允许使用一次。开始留出前写入 `examples/20-gepa-offline-optimization/output/holdout-exposure-demo-v2-20260926.json`；中断也算已使用。不能删除标记继续调候选，再把同一批题叫作独立留出。新一轮正常优化须先准备新的数据版本。

## 原理与限制

搜索仍调用官方 `gepa.optimize`。课程提供 Hermes 执行适配器、评分和停止条件；`Adapter.propose_new_texts = None`，提案和搜索由官方 GEPA 执行，没有自写搜索回退。反思提示要求精炼原文，目标300—420字符且不增长；返回的候选原样检查和执行，不事后截断。硬性检查仍限制正文最多增长20%，文件头与字段说明固定。

每题独立启动 Hermes，只开放只读的 `query_payment`。保留 `tools.tool_search.enabled: false`，直接呈现课程工具。完整 Skill 和参考文件通过真实加载函数预载，运行前后核对哈希。基线明确要求纯 JSON、不加分析或代码围栏，理由中不嵌未转义双引号。评分器直接解析原始最终回答，不修复 JSON。

预算上限为30次开发评分，其中预留3次做送检候选的完整验证；搜索最多2个新提案、600秒，每题最多120秒。官方 `NoImprovementStopper(2)` 监测每次检查时的验证最佳分数停滞，连续2次检查无改善不表示又完成2次完整验证。留出另预留6次执行，公开故障题另预留2次。评分次数不等于模型调用次数；反思请求、Hermes 调用报告和实际 HTTP 请求分别记录。网络瞬时429、502、503、504可按记录的退避重试，401不等待；这不改变业务分数。

官方 GEPA 要求候选在训练小批次严格胜过父版本才继续接受。在已经满分的基线上，短候选可能同样全部答对，却仍被官方拒绝。为让读者看见后续检验，运行前约定：官方产生新赢家时就检验赢家；官方仍选基线时，从静态合格且已经真实训练评估的提案里选最短者，补跑完整验证，再送入独立留出。后一条明确称为“GEPA 提案的教学审计”，不是官方赢家，也不是算法已经接受的候选。选择过程不读取留出；若没有合格的真实提案，就报告不完整，不能造一个成功结果。

采用要求两侧执行完整、候选所有硬性字段通过、整体与各类分数不下降、均分至少提高0.02，候选平均 token 不超过12000、单题不超过120秒。相同版本、平分、缺失用量、执行故障都不能作为新版本改善。实际费用尚须查看供应商账单，估价未知不能写成零费用。

固定坏候选故意只按订单号取首条记录、忽略商户。它来自教学配置，独立于 GEPA 搜索和最终留出；通过真实 Hermes 调用观察是否出现跨商户误判，再用同一采用函数判断。若未出现预期退步，也照实保留。“训练集赢、留出输”留作思考题，本练习不额外制造一条搜索故事。

`service-version.json` 只是本地版本指向记录。练习不发布在线服务、不推送 Git，也不写飞书。运行结束再次核对基线哈希及预载。

## 从哪里核对

每轮 `manifest.json` 保存事前预算、候选选择规则、数据哈希、依赖版本和实际计数；`source/` 保存该轮运行时脚本。`events.jsonl` 连续记录真实评分、反思输入输出、官方接受或拒绝及最终送检身份。`official-winner.json` 与 `selected-candidate.json` 分别说明官方赢家和送检候选，不能混用。

结果按 `baseline-development.json`、`candidate-development.json`、`holdout-comparison.json`、`adoption.json`、`fault-injection.json` 顺序阅读。逐题 `executions/` 保存原始响应、真实工具轨迹、HTTP状态、用量和评分。历史联网结果留在内部重设计目录；本轮新运行以公开入口的独立运行目录为准。

## 新环境安装与补充比较

```bash
python3.12 -m venv examples/20-gepa-offline-optimization/.venv
examples/20-gepa-offline-optimization/.venv/bin/python -m pip install -r examples/20-gepa-offline-optimization/requirements-optimizer.lock.txt
```

优化器锁定 `gepa==0.0.27`，Hermes 依赖另列在 `requirements-hermes-observed.txt`，默认源码提交 `aaf9688519cca58dd5f76a589a0911aff269b060`；`HERMES_SRC` 与 `HERMES_PYTHON` 可指定同版源码和解释器。锁文件是已观察环境的版本列表，平台依赖可能需同系统安装。无需读取个人 `.env`；只显式导出凭证即可。

本练习由官方 GEPA 负责搜索，由课程适配器调用真实 Hermes。官方搜索可能仍选基线；为观察后续检查，练习还会按事先约定，从已真实评估的合格提案中选取1个作教学审计。它不是官方赢家。报告分别标明“官方赢家”“教学审计提案”“最终采用版本”；采用资格通过也只进入发布流程，本脚本仍保持正式基线。

0.02 是当前0—1逐题得分均分的绝对差，对应2个百分点；平均12000 Token是成本代理指标，真实费用按供应商账单核对。

`gepa_offline.py` 保留为旧自建教学循环的补充比较，不是官方 GEPA 主练习。观察时先确认被检查文件的身份，再从反思输入、提案、真实执行、独立留出追到采用决定。

## 工程应用与观察练习

预算包括开发评分、反思、单题时限与独立留出，评分次数不是 HTTP 请求次数。官方仍选择基线时，送检的短提案明确标为教学审计，不冒充官方赢家。平分、token 缺失、执行不完整均不能证明新版本改善。留出开始即记 exposure，中断也不可删除标记后继续调参复用。

选一个用例，保存完整输入、版本、原始输出与评分。注入缺失回执或错对象的反例，说明它在哪一层被拒绝；若未拒绝，保留为待修复问题。
