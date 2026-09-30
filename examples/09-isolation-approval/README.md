# 第 09 讲：隔离与审批

本练习是**预设配置边界观察和独立 Thread 的批准/拒绝对照**。先比较全只读与 `outputs/` 可写的范围，再让两条新 Thread 对同一条报告命令分别经历拒绝与一次性批准。权限草案生成列为可选扩展。

## 前置条件与运行

使用 Python 3.12 内核。从仓库根目录启动 JupyterLab：

```bash
python -m pip install jupyterlab ipykernel
python -m jupyterlab examples/09-isolation-approval/workshop.ipynb
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

## 完整调用位置：Python SDK

公共准备创建本次 `inputs/`、`outputs/`、输入文件和 SDK 连接。第 1 节完整展示会话配置、任务输入及启动位置，统一使用 SDK 参数：

```python
readonly = lab.codex.thread_start(
    cwd=str(WORK), sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all,
)
probe_result = lab.run(readonly,
    f"原样执行一次教学探针：`{probe_command}`。记录结果，不重试、不申请扩权。")
allowed = outputs / "allowed.txt"
scoped_command = f"printf allowed > {shlex.quote(str(allowed))} && {probe_command}"
scoped = lab.codex.thread_start(
    cwd=str(outputs), sandbox=Sandbox.workspace_write, approval_mode=ApprovalMode.deny_all,
)
scoped_result = lab.run(scoped,
    f"目标是根据 {protected} 完成一份简短支付告警分析，写到当前目录 incident-report.md。"
    f"先原样执行一次教学边界探针：`{scoped_command}`。"
    "如果修改输入被拒绝，保留原文件，不重试写入、不申请扩权；继续只读分析，在当前允许目录完成报告。")
```

以上变量均在 notebook 公共准备或第 1 节定义；运行从第一格开始，不把代码片段脱离准备区单独执行。`lab.run()` 调用 `Thread.turn()` 并等待结束，保留请求与结果。可写会话的 `cwd` 是 `outputs/`，相邻 `inputs/` 不在该目录的可写范围；共用配置排除临时目录例外并关闭工具网络访问。

这里不混用 `turn/start.params`、CLI 和 TOML 字段。[App Server 官方接口说明](https://learn.chatgpt.com/docs/app-server#turns) 可用于对照底层协议；本练习具体参数以固定版本的 Python SDK 为准。

## 看什么

边界探针必须有配对命令、退出码和明确的目标权限拒绝；宿主阻止 Sandbox 启动、路径不存在或命令没有执行，都不能算权限拒绝。查看 `outputs/allowed.txt`、受保护输入哈希与拒绝后的分析报告，判断任务实际怎样继续。

第 2 节使用独立客户端与审批回调：Thread 1 拒绝，Thread 2 允许。不是同一个任务先拒绝再批准。回调只允许固定目录、命令和脚本哈希匹配的一次操作；核对实际审批记录、工具回执、报告结果和批准是否消耗。批准不证明报告内容正确。

## 可选扩展与边界

`RUN_PERMISSION_PLAN` 默认关闭，启用后只生成权限配置建议草案，不自动应用。当前哈希核对与执行间仍有时间间隙，没有验证生产级并发替换防护；本例也没有单独验证网络边界。

## 本次修订的核查范围

本次完成静态检查和相应离线核查，未重新执行完整付费模型实验。`validation/` 保留此前版本的实跑记录，不能据其中的 `passed` 判断当前 notebook 已实跑通过；应按记录中的版本和哈希区分。
