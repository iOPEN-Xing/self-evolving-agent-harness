# 第 08 讲：渐进披露

分别追踪 Skill 描述、正文、参考文件及脚本输出，查看它们何时进入真实请求。正文加载由显式 `SkillInput` 控制；本例不能证明自动匹配 Skill 可靠。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/08-progressive-disclosure/workshop.ipynb
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

普通问候先观察名称描述，正文和参考内容是否已经加载，以请求对照表为准。显式启用 Skill 后，核对正文标记；任务需要阈值时，再请 Agent 读取参考文件，沿工具回执查看参考内容是否进入后续输入。

配套 `scripts/check_report.py` 已在本次离线核查中执行成功。Notebook 第 4 节让 Agent 执行同一脚本，显示命令回执和输出标记；脚本**只统计报告长度（字符数），不检查引用完整性，也不评分结论质量**。若本次未成功执行，照实记录，不能仅凭模型回答宣布脚本已完成。

## 观察边界

脚本输出可以进入后续请求，源码是否进入另看源码标记；Agent 如果自行读取源码，表里会显示。本例用显式 `SkillInput` 控制正文加载时点，不评测自动匹配的可靠性，也不据此声称所有资源类型都已验证。参考标记用于检查，不要求回答逐字引用。

## 本次修订的核查范围

本次完成静态检查和相应离线核查，未重新执行完整付费模型实验。`validation/` 保留此前版本的实跑记录，不能据其中的 `passed` 判断当前 notebook 已实跑通过；应按记录中的版本和哈希区分。
