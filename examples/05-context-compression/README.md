# 第 05 讲：上下文压缩

通过原生压缩事件和真实请求，比较压缩前后保留的任务状态；再追问摘要未必保留的采样细节，观察 Agent 是否发现信息缺口并主动回查。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/05-context-compression/workshop.ipynb
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

先让 Agent 读取 `work/capacity.log`，再补充排除 CPU 饱和、先核对连接池配置且不得重启的要求。请求 `compact()` 后，等待新增的 `contextCompaction` Turn 完成，不能把接口返回当作压缩完成。

复述任务状态时，人工核对目标、纠正、下一步与操作限制；分别查看当前模型输入、完整 Thread 历史和磁盘原文。输入字符数只表示长度，不等于 token 数或费用，也不保证每次压缩后都减少。

最后只追问 `sample=0073` 与 `sample=0127` 的 `elapsed_us` 及差值，不发送原文或指定读取命令。沿新增工具回执看它是否主动回查；摘要已保留细节、未回查或回答不足，均照实记录。核对回答中的数值与原文件，不预设模型一定补读。

## 观察边界

Notebook 不充当外部采集器替 Agent 重新投递日志，也没有实现任务状态、因果判断或数值回答的语义断言。压缩完成检查、原句是否出现、字符数与文件变化检查，只支持各自对应的观察，不能据此承诺任务语义完整保留。

## 本次修订的核查范围

本次完成静态检查和相应离线核查，未重新执行完整付费模型实验。`validation/` 保留此前版本的实跑记录，不能据其中的 `passed` 判断当前 notebook 已实跑通过；应按记录中的版本和哈希区分。
