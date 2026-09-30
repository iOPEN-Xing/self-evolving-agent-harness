# 第 01 讲：从告警开始一次调查

对应第 01 讲支付接口 HTTP 500 案例。练习入口为 [examples/01-agent-loop/workshop.ipynb](workshop.ipynb)：把告警、日志和代码交给 Codex，只说明目标与边界，不指定读取顺序，让模型根据工具返回继续决定下一步。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/01-agent-loop/workshop.ipynb
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

第 2 节开始调用模型。查看 Thread ID、Turn ID，以及工具命令、返回信息、退出码和最终回答；沿相邻事件找出“刚获得的线索如何影响后续动作”。模型可能合并读取文件，按实际轨迹记录。

最后阅读 `work/outputs/incident-report.md`，检查结论能否回到日志或代码位置；同时核对 `work/inputs/` 中的告警、日志和代码是否改变。Turn 完成或报告存在，均不能证明归因正确。

## 范围与失败处理

这是构造的教学材料，不连接生产系统。输入不变是任务要求与事后检查；`workspace_write` 允许写工作目录，第 09 讲再观察目录级权限限制。

第一次请求若返回 401，适配器可能随后进入冷却期，使 SDK 最后只显示 429。Notebook 会检查本次 `adapter.log` 并提示可能的鉴权失败，不打印完整日志或凭证。检查 GLM API Key 和普通 API 权限后再重跑。

## 接口参考

- [Codex Python SDK](https://github.com/openai/codex/tree/main/sdk/python)：本例使用的会话、Turn 与事件接口。
- [GLM-5.2 调用说明](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)：模型名与普通 API 端点。
- [LiteLLM](https://github.com/BerriAI/litellm)：本机协议适配器。
