# 第 07 讲：经验技能化

默认练习从实际教学对话和纠正中提炼候选 `SKILL.md`，在同一新案例上比较无 Skill 与有 Skill 的独立 Thread。入口：[workshop.ipynb](workshop.ipynb)。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/07-skill-extraction/workshop.ipynb
```

从第一格依次运行 [workshop.ipynb](workshop.ipynb)。公共准备格自动补装 `openai-codex==0.154.0`、`litellm[proxy]==1.101.0`；已导入其他版本时，安装后须重启内核。也可提前安装 `examples/requirements-notebooks.txt`。

统一使用 `GLM_API_KEY`，未设置时 notebook 弹出隐藏输入框。若希望提前设置，可在启动 JupyterLab 前执行：

```bash
read -r -s GLM_API_KEY
export GLM_API_KEY
```

需要 GLM-5.2 普通 API 权限；模型调用会产生费用，无需 OpenAI 或 ChatGPT 登录。

## 运行目录与重跑

按照 `examples/notebook_support.py`，每次完整运行在本讲目录下新建 `.runtime/<运行编号>/`，输入和报告放在其中的 `work/`，模型请求、Turn 记录与适配器日志放在运行目录内。它不是系统临时目录，关闭连接不会删除记录。可在 notebook 中查看 `lab.runtime` 定位本次目录。

失败或重跑前先执行最后的清理格，再从公共准备开始；不要把上次报告当成本次结果。运行目录由 Git 忽略，分享前清除 notebook 执行输出并检查记录中的本机路径与业务材料。

## 看什么

读提炼出的每条方法：哪些来自这次纠正，哪些是模型额外补充的要求？格式检查只核对 YAML 头、调用名称、非空描述和正文，不判断方法质量，也未实现旧现场信息全部清除或根因语义正确的自动断言。

新任务使用相同现场与提示；有 Skill 的运行通过 `SkillInput` 显式加载候选方法。比较输入和实际回答中的依据、下一步与不确定性；若没有清晰差别，如实记录，不能只因运行完成就宣布 Skill 有效。

## 可选：初始化目录骨架

默认流程没有调用系统 Skill Creator。Notebook 末尾的 `RUN_CREATOR_INIT` 默认关闭；启用后调用本机 `init_skill.py`，在本次 `work/creator-demo/` 生成独立演示骨架，不安装个人 Skill，不覆盖默认候选。

也可在本目录的终端中运行以下命令；本机安装路径不同时，先调整 `SKILL_CREATOR_ROOT`：

```bash
SKILL_CREATOR_ROOT="${SKILL_CREATOR_ROOT:-$HOME/.codex/skills/.system/skill-creator}"
SKILL_DEMO_DIR=".runtime/creator-demo-$(date +%Y%m%d-%H%M%S)"
python3 "$SKILL_CREATOR_ROOT/scripts/init_skill.py" legacy-payment-investigation \
  --path "$SKILL_DEMO_DIR" \
  --resources scripts,references \
  --interface 'short_description=调查 legacy-payment 模块告警，核对日志、指标与运行配置'
```

已按 `skill-creator/scripts/generate_openai_yaml.py` 核对：`short_description` 必须为 25—64 个字符；修正后的说明为 35 个字符。目录初始化与格式校验均不能证明方法适用于新事故。

## 本次修订的核查范围

本次完成静态检查和相应离线核查，未重新执行完整付费模型实验。`validation/` 保留此前版本的实跑记录，不能据其中的 `passed` 判断当前 notebook 已实跑通过；应按记录中的版本和哈希区分。
