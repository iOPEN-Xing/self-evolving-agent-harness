# 第 02 讲：巡检途中，把告警交给活跃 Turn

打开 [examples/02-instruction-control/workshop.ipynb](workshop.ipynb)，观察 `steer()` 如何向同一个正在运行的 Turn 追加告警，再独立观察 `interrupt()`。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/02-instruction-control/workshop.ipynb
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

## 场景与观察

本例只有两台服务器：先巡检 `server-17 / payment-api`，读取部署与监控；处理告警后继续巡检唯一的后续对象 `server-31 / order-api`。

慢探针留出约 25 秒的告警窗口。确认 `server-17` 两份材料的成功读取回执和探针标记后，才调用 `TurnHandle.steer()`。告警不直接提供连接池变更答案。重点检查告警是否进入原 Turn、调查是否利用先前材料、是否继续后续巡检，以及输入有没有改变。

随机编号仅用于辨认完整读取回执，不要求报告逐字引用。读取采用独立 `cat`、不用管道，是本例的观察条件。回执不全、没有取得执行窗口、后台异常或超时会停止本次实验；实际动作缺失时照实记录，不用报告中的字样代替工具执行。

公共准备包含一次简短模型连通检查，会产生少量 API 费用。第 4 节用全新 Thread 独立演示中断；只观察中断时，可在公共准备后直接跳到该节。

## 记录与边界

本次 `patrol-events.json`、`patrol-observations.json` 和 `interrupt-events.json` 保存在 `.runtime/<运行编号>/`；故障、巡检和中断报告若生成，放在 `work/`。第 3 节展示实际时间线、报告和输入变化，观察记录不自动判定根因正确。

`interrupt()` 请求结束当前 Turn，不保证杀死已启动的探针，也不会回滚已经发生的副作用。探针只写本次观察标记，不操作业务服务。本例不把正文中的 `stop` 等同于统一的“关闭 Thread”接口。

离线检查从仓库根目录执行：

```bash
python examples/02-instruction-control/test_control_validation.py
```

配套 [control_validation.py](control_validation.py) 与 [test_control_validation.py](test_control_validation.py) 检查事件识别和等待逻辑，不调用模型，也不代替 notebook 的现场观察。
