# 练习导航：按同一条问题链阅读

完整章节表见 [课程与代码对照](../COURSE_MAP.md)，环境步骤见 [模型与依赖准备](../docs/MODEL_SETUP.md)。

01–03 先学会观察 Agent 如何行动、怎样接收执行中信息、文件何时进入上下文；04–09 再解决长任务中的规则、压缩、分支、方法加载与权限。10 章桥接执行与学习；11–17 分开研究 Session、Memory、Provider、Skill 和 Curator；18–20 建立业务评分、可重跑数据集与独立候选验证；21–23 再观察采集、发布、加载与恢复。综合实践将它们换成原生 Hermes 支付任务。

## Notebook 路线

从 [第 01 章](01-agent-loop/README.md) 开始，每讲 README 对应 workshop.ipynb 的稳定单元 ID。使用 Python 3.12 与 openai-codex==0.154.0，模型为 deepseek-flash。未设置 DEEPSEEK_API_KEY 时公共准备用隐藏输入框；不读取其他供应商密钥。

[notebook_support.py](notebook_support.py) 启动课程独立连接、保留脱敏请求与 Turn、配对命令回执、处理超时和释放资源。观察服务原样转发 Responses 到官网，已移除 LiteLLM 转换。每次 .runtime/run_id 权限为当前用户私有，保存本次 work 和请求，清理连接不删除证据。

先检查真实请求与回执，再读最终回答。运行无异常、目录有文件、语义判断正确分别验收。可选开关默认关闭，不将默认运行结果写成可选路径已验证。

## 脚本路线

11–23 的推荐入口见各章 README；第 17 章生命周期不调用模型，第 18 章直接比较指令而不启动 Hermes；第 19 章用原生 skill-up 编排自定义引擎，第 20 章由官方 GEPA 搜索，第 23 章前台是自建工具循环。不要因为共用 Hermes 解释器就称它们都运行了原生 Agent。

后半程运行使用 .deps/hermes-agent/.venv/bin/python。第 20 章优化器有独立锁文件与环境；第 17 / 19 章 OS 沙箱目前依赖 macOS。原生 [Assembly](assembly/README.md) 与 [Capstone](capstone/README.md) 是另两条有明确阶段和证据的路线。

## 生成、证据与复核

[build_notebooks.py](build_notebooks.py) 是早期生成器，保留作来源参考；当前读者版 Notebook 有额外的阅读和观察设计，不要运行它覆盖已有 workshop。教学源 Notebook 不提交执行输出。

新输出默认保留在本机独立运行目录。仓库正文中提到的旧内部验收材料不一定随代码发布；本轮公共证据以 [验证报告](../docs/DOCUMENTATION_VALIDATION.md) 为准。历史 GLM 输出不能作为当前 DeepSeek 的通过证明，当前失败记录也不能用旧轮成功片段补齐。

make verify 检查语法、数据格式、文档链接、代码入口和离线行为，付费模型和 OS 边界另验。源码版本、模型协议、输入哈希、工具回执、候选来源与独立评分一起形成可复核记录。
