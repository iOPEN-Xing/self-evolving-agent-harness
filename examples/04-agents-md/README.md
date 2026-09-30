# 第 04 讲：AGENTS.md

对应正文观察题：先在旧 Thread 中纠正因果判断，再让新 Thread 分析另一场事故；随后把同一句要求写入 `AGENTS.md`，比较新任务的输入与回答。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/04-agents-md/workshop.ipynb
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

第 1 节比较旧任务初答、纠正后的回答和未写规则的新任务；查新任务输入有没有自动带入旧纠正。模型可能本来就没有犯错，或新任务也能作出谨慎判断，均如实记录。

第 2 节把同一句因果判断要求写进工作目录根部的 `AGENTS.md`，在 `payment-service/AGENTS.md` 增加支付服务核对要求。对同一场新事故，分别从根目录与支付服务目录创建 Thread，核对两级规则的加载范围，再比较回答。

撤下局部规则后的新 Thread 是可选观察；`RUN_REMOVE_LOCAL` 默认关闭，只有启用后才会运行。它不证明旧 Thread 会即时刷新规则。

## 观察边界

当前 notebook 使用自然语言规则，不使用临时随机编号验收。规则进入输入不等于因果判断一定正确；`AGENTS.md` 也不提供文件写保护。

## 本次修订的核查范围

本次完成静态检查和相应离线核查，未重新执行完整付费模型实验。`validation/` 保留此前版本的实跑记录，不能据其中的 `passed` 判断当前 notebook 已实跑通过；应按记录中的版本和哈希区分。
