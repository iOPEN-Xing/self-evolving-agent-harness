# 第 03 讲：上下文引擎

文件可以被读取，与文件内容已经进入模型请求，是两件事。练习对应正文观察题：先只依据告警列出事实，再在同一 Thread 读取部署记录，回答同一个问题。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/03-context-engine/workshop.ipynb
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

第一次回答不调用工具；核对首个请求没有部署文件的采集编号。第二次要求读取 `work/deployment.md`，沿命令和回执查看文件内容何时进入后续请求，再比较两次回答中的事实、猜测和缺失信息。

最后在同一 Thread 追问采集编号与时间，不要求再次读取；核对前轮回执是否仍在、有没有新的工具调用、文件是否保持不变。可选 `RUN_NEW_THREAD` 默认关闭，用于比较全新 Thread 的输入，不靠两次回答相似就判断看过相同材料。

## 观察边界

展示的是 Codex 发给本地适配器的请求，不代表模型内部状态。编号帮助定位信息来源，不能证明结论正确；模型未按要求读取时，如实记录实际回执。

## 本次修订的核查范围

本次完成静态检查和相应离线核查，未重新执行完整付费模型实验。`validation/` 保留此前版本的实跑记录，不能据其中的 `passed` 判断当前 notebook 已实跑通过；应按记录中的版本和哈希区分。
