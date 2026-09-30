# 第 06 讲：会话分支

基础练习只用一条父 Thread：Turn 1 放模块背景，Turn 2 放旧事故结论，再从 Turn 1 分岔。核对继承边界，而不是让多个父 Thread 反复调查来比较回答。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/06-thread-fork/workshop.ipynb
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

## 基础观察

固定 SDK 的高层 `thread_fork` 未暴露 `last_turn_id`，本例通过 `CodexClient.thread_fork` 使用 `lastTurnId=base.id`；所选 Turn 包含在继承范围内。

默认只执行父 Thread 两个 Turn 与子 Thread 一个 Turn。比较父历史和子首次实际请求：父含背景与旧结论，子应含背景与新任务、排除 Turn 2 的旧结论。再核对分支来源和父历史是否保持原样。输入与回答均以实际结果为准。

## 可选观察

- `RUN_CONTAMINATED_BACKGROUND`：在 Turn 1 同时放入旧根因，完整重跑后核对它是否被继承；仍只用一条父 Thread。
- `RUN_SECOND_BRANCH`：从同一父 Thread 再建立 B 分支，检查 A 的新任务是否串入 B；多一次模型调用。
- `RUN_SHARED_FILE`：子写便笺、父再读取，多两次模型调用，用于观察共享目录。

三个开关默认都为 `False`；基础观察完成后直接运行最后的清理格。

## 观察边界

`Fork` 截取历史，不保证继承内容适用于新事故。父子仍共用 `cwd`，不提供文件快照；需要文件隔离时须另外配置工作目录或 Sandbox。

## 本次修订的核查范围

本次完成静态检查和相应离线核查，未重新执行完整付费模型实验。`validation/` 保留此前版本的实跑记录，不能据其中的 `passed` 判断当前 notebook 已实跑通过；应按记录中的版本和哈希区分。
