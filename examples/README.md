# 第 03 至 09 讲代码练习

这七份引导练习依次检查上下文装配、项目规则、压缩、历史分支、经验提炼、按需加载与执行权限。它们为后半程的后台学习、方法修订、业务评测和跨实例共享准备可观察的运行基础。业务输入均为明确构造的教学材料，模型调用、Codex 会话与工具执行使用真实接口。

| 讲次 | 练习入口 | 核对的机制 |
| --- | --- | --- |
| 03 | [上下文引擎](03-context-engine/README.md) | 规则与工具进入请求，文件内容经成功回执进入后续调用 |
| 04 | [AGENTS.md](04-agents-md/README.md) | 根到当前目录的规则装配，以及新任务的规则范围 |
| 05 | [上下文压缩](05-context-compression/README.md) | 原生压缩事件、输入缩短与任务判断状态保留 |
| 06 | [会话分支](06-thread-fork/README.md) | 指定 Turn 为包含边界，保留模块背景，排除后续旧结论 |
| 07 | [经验技能化](07-skill-extraction/README.md) | 实际经历生成候选方法，独立新任务通过 SkillInput 使用 |
| 08 | [渐进披露](08-progressive-disclosure/README.md) | 描述、正文与参考资料分阶段进入请求 |
| 09 | [隔离与审批](09-isolation-approval/README.md) | 只读、局部可写，以及具体命令的一次性审批 |

## 前置条件与运行

使用 Python 3.12。固定模型侧版本为 `openai-codex==0.154.0` 和 `litellm[proxy]==1.101.0`。`requirements-notebooks.txt` 同时固定 nbformat、nbclient 和 ipykernel。GLM-5.2 使用普通 API，需要相应调用权限，运行会产生 API 费用。

从项目根目录在选定的虚拟环境执行：

```bash
python -m pip install -r examples/requirements-notebooks.txt jupyterlab
read -r -s GLM_API_KEY
export GLM_API_KEY
python -m jupyterlab examples/
```

`read` 隐藏输入凭证，再将它导出到当前进程环境。不要把凭证填入 Notebook 或提交到仓库。七讲只读取 `GLM_API_KEY`，缺失时直接报错，不等待交互输入，也不读取备用变量。选择同一 Python 3.12 内核，从上到下运行。

若某个单元格失败，先运行最后的关闭单元格，再从头重跑。每次连接使用新的运行目录，无需保留上次执行输出。

第 03、08、09 讲需要宿主支持 Codex 原生 Sandbox。第 09 讲将可写范围限定在教学输出目录。若宿主禁止嵌套 Sandbox，启动错误会使练习失败，不能解释为写入边界生效。

## 文件与输出

- `workshop.ipynb`：不含执行输出的教学源文件，开头列出本讲核心代码及其位置。运行后的单元格输出可随 Notebook 手动保存，分享前清除输出并检查敏感信息。
- `.runtime/<运行编号>/`：原始请求、命令与会话记录、教学工作目录和适配器日志，由 Git 忽略。此目录权限为当前用户私有，不作为公开附件。
- [build_notebooks.py](build_notebooks.py)：Notebook 生成脚本；直接打开现有练习无需运行它。
- [notebook_support.py](notebook_support.py)：负责连接、请求记录、命令回执配对、超时处理与进程释放，运行 Notebook 时需要保留。

## 协议与观察边界

`glm-codex` 是本地别名，远程模型为 `glm-5.2`。本地适配器使用回环地址和随机令牌，将 Codex 的 Responses 请求转为 GLM Chat Completions。凭证保留在连接进程环境中，命令环境排除凭证字段，公开输出隐藏密钥、本机路径和真实会话编号。

请求记录显示 Codex 在协议转换前提供的内容。对未带 `call_id` 的 ExternalMessage，适配器补充配对信封以保留工具级资料，不提升为用户授权，也不表示模型实际发起了该工具调用。第 05 讲明确由 Notebook 扮演外部采集器。

接口核对依据是本机固定版本的 SDK 签名与协议类型，并对照 [Codex App Server 官方文档](https://developers.openai.com/codex/app-server)、[AGENTS.md 规则说明](https://developers.openai.com/codex/guides/agents-md)和 [Skill 官方说明](https://developers.openai.com/codex/skills)。官方页面会更新，当前练习不能因文档变化跳过固定版本实跑。
