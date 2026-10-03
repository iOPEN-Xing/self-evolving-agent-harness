"""生成第 03 至 09 讲的干净 notebook；不调用模型。

修改本文件中的对应单元格后运行本脚本；生成结果不含保存的执行输出。
"""
from pathlib import Path
import nbformat as nb

ROOT = Path(__file__).resolve().parent


def write_lesson(slug, cells):
    notebook = nb.v4.new_notebook(cells=cells, metadata={
        "kernelspec": {"display_name": "Python 3.12", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"}})
    for index, cell in enumerate(notebook.cells):
        cell.id = f"lab{slug[:2]}-{index:02}"
    nb.validate(notebook)
    directory = ROOT / slug
    directory.mkdir(exist_ok=True)
    nb.write(notebook, directory / "workshop.ipynb")


def lesson03():
    write_lesson('03-context-engine', [
        nb.v4.new_markdown_cell(r'''# Lab 03｜文件在磁盘上，模型就已经知道了吗？

继续前两讲的巡检现场：同一句调查要求，模型第一次调用时还没有日志正文。等工具返回以后，这段内容才进入下一次调用。我们直接查看实际请求，区分任务、固定规则、工具说明与工具结果。'''),
        nb.v4.new_markdown_cell(r'''## 1. 准备运行环境

使用 Python 3.12、openai-codex 0.154.0 的原生 Responses 协议 调用 DeepSeek Flash 普通 API，无需 OpenAI 登录。从项目根目录执行 `python -m pip install -r examples/requirements-notebooks.txt jupyterlab`，再用同一环境启动 Jupyter。真实模型调用会产生 API 费用。

`examples/notebook_support.py` 负责连接、请求记录和关闭进程，本讲机制在下面的单元格中直接展开。保留完整的 `examples/` 目录。教学材料每次写入新的 `.runtime/` 子目录；其中的原始日志供本机排查，不对外分享。'''),
        nb.v4.new_code_cell(r'''from pathlib import Path
import sys

# 从项目根目录或本讲目录启动 Jupyter 均可。
project = next((p for p in [Path.cwd(), *Path.cwd().parents]
                if (p / "examples" / "notebook_support.py").is_file()), None)
if project is None:
    raise RuntimeError("请在项目根目录或 examples 的子目录内启动 Notebook。")
sys.path.insert(0, str(project / "examples"))
from notebook_support import (Lab, ApprovalMode, Sandbox, tool_output_contains,
                              command_receipts, denied_write, exact_command, read_json_answer)
LESSON = project / "examples" / "03-context-engine"
print("依赖与项目位置已确认。")'''),
        nb.v4.new_markdown_cell(r'''## 2. 连接 DeepSeek Flash

在启动 Jupyter 或验证器以前设置 `DEEPSEEK_API_KEY` 环境变量。本练习只读取这个变量；缺失时立即报错。密钥不写进 notebook，也不传给 Agent 的命令环境。下面启动本地适配器，`lab.run()` 开始真实模型调用。'''),
        nb.v4.new_code_cell(r'''if "lab" in globals():
    lab.close()
lab = Lab(LESSON)
WORK = lab.work
print("DeepSeek Flash 适配器已连接；材料保存在本讲 .runtime 中。")'''),
        nb.v4.new_markdown_cell(r'''## 3. 放入一条模型尚未读到的现场记录

每次生成不同的采集编号。这个编号只写入文件，不出现在任务和开发者说明中。它能帮助我们确定：模型是在什么时候真正收到这条记录的。'''),
        nb.v4.new_code_cell(r'''import secrets
marker = "SAMPLE-" + secrets.token_hex(6)
(WORK / "metrics.txt").write_text(
    f"采集编号：{marker}\n采集时间：02:07\n服务：payment-api\n连接池等待：3.6s\n", encoding="utf-8")
input_before = (WORK / "metrics.txt").read_bytes()
rule = "汇报时必须同时给出材料文件名、采集时间与采集编号；不要修改文件。"
thread = lab.thread(developer_instructions=rule)
start = len(lab.requests)
result = lab.run(thread, "读取 metrics.txt，报告其中的异常。不要查询其他文件或网络；工具失败后立即停止，不重试、不换其他命令。")'''),
        nb.v4.new_markdown_cell(r'''## 4. 打开每一次调用的工作现场

 这里记录的是 Codex 交给本地适配器的 Responses 请求体，尚未经过 LiteLLM 的协议转换。它能显示 Harness 的装配结果；不代表模型内部状态，也不显示模型的隐藏推理。'''),
        nb.v4.new_code_cell(r'''import json
lab.show_requests(start)
calls = lab.requests[start:]
assert len(calls) >= 2, f"读取应产生后续调用，实际捕获 {len(calls)} 次请求。"
for index, request in enumerate(calls, 1):
    text = json.dumps(request, ensure_ascii=False)
    print(f"调用 {index}：固定规则={rule in text}；现场编号={marker in text}")

commands = [item.root for item in result.items if item.root.type == "commandExecution"]
for item in commands:
    print("命令：", lab.clean(item.command))
    print("返回：", lab.clean(item.aggregated_output))
    print("退出码：", item.exit_code)'''),
        nb.v4.new_markdown_cell(r'''## 5. 在同一 Thread 继续一轮

 第二个用户输入不重复采集编号，也不要求重新读取文件。观察前面取得的工具结果怎样跟随 Thread 进入这一轮。'''),
        nb.v4.new_code_cell(r'''followup_start = len(lab.requests)
followup = lab.run(thread, "根据刚才已经取得的记录，只回答采集编号和采集时间。不要再调用工具。")
checks = {
    "首次请求包含真实工具定义": bool(calls[0].get("tools")),
    "任务输入确实进入首次请求": "读取 metrics.txt" in json.dumps(calls[0].get("input"), ensure_ascii=False),
    "输入文件保持不变": (WORK / "metrics.txt").read_bytes() == input_before,
    "追问没有再次执行命令": not ({r["call_id"] for r in command_receipts(lab.requests[followup_start:])} - {r["call_id"] for r in command_receipts(calls)}),
    "第一次调用有固定规则": rule in json.dumps(calls[0], ensure_ascii=False),
    "第一次调用尚无文件中的随机编号": marker not in json.dumps(calls[0], ensure_ascii=False),
    "读取命令实际成功并返回编号": any(c.exit_code == 0 and marker in (c.aggregated_output or "") for c in commands),
    "后续调用收到工具结果": any(tool_output_contains(c, marker) for c in calls[1:]),
    "同一 Thread 下一轮仍收到编号": marker in lab.request_text(followup_start),
    "最终回答保留编号和时间": marker in followup.final_response and "02:07" in followup.final_response,
}
record = lab.verify(checks)'''),
        nb.v4.new_markdown_cell(r'''## 关闭本地进程'''),
        nb.v4.new_code_cell(r'''lab.close()
print("Codex 与本地适配器已关闭，运行材料保留供检查。")'''),
        nb.v4.new_markdown_cell(r'''## 继续观察

把同一句追问交给全新的 Thread，但不要在输入中透露编号。它还能准确回答吗？同时比较新旧请求；不要只凭回答语气判断它是否见过材料。

如果前面的单元格报错，也请运行关闭单元格。分享前清除执行输出；`.runtime/` 包含本机路径和请求记录，已由 Git 忽略。干净 notebook 和脱敏验证摘要分开保留。'''),
    ])

def lesson04():
    write_lesson('04-agents-md', [
        nb.v4.new_markdown_cell(r'''# Lab 04｜规则怎样进入一个全新的 Thread？

上一讲的 developer_instructions 跟随指定 Thread。这次把规则放到 AGENTS.md，分别从项目根目录、数据库目录启动新 Thread，直接检查规则的加载范围。'''),
        nb.v4.new_markdown_cell(r'''## 1. 准备运行环境

使用 Python 3.12、openai-codex 0.154.0 的原生 Responses 协议 调用 DeepSeek Flash 普通 API，无需 OpenAI 登录。从项目根目录执行 `python -m pip install -r examples/requirements-notebooks.txt jupyterlab`，再用同一环境启动 Jupyter。真实模型调用会产生 API 费用。

`examples/notebook_support.py` 负责连接、请求记录和关闭进程，本讲机制在下面的单元格中直接展开。保留完整的 `examples/` 目录。教学材料每次写入新的 `.runtime/` 子目录；其中的原始日志供本机排查，不对外分享。'''),
        nb.v4.new_code_cell(r'''from pathlib import Path
import sys

# 从项目根目录或本讲目录启动 Jupyter 均可。
project = next((p for p in [Path.cwd(), *Path.cwd().parents]
                if (p / "examples" / "notebook_support.py").is_file()), None)
if project is None:
    raise RuntimeError("请在项目根目录或 examples 的子目录内启动 Notebook。")
sys.path.insert(0, str(project / "examples"))
from notebook_support import (Lab, ApprovalMode, Sandbox, tool_output_contains,
                              command_receipts, denied_write, exact_command, read_json_answer)
LESSON = project / "examples" / "04-agents-md"
print("依赖与项目位置已确认。")'''),
        nb.v4.new_markdown_cell(r'''## 2. 连接 DeepSeek Flash

在启动 Jupyter 或验证器以前设置 `DEEPSEEK_API_KEY` 环境变量。本练习只读取这个变量；缺失时立即报错。密钥不写进 notebook，也不传给 Agent 的命令环境。下面启动本地适配器，`lab.run()` 开始真实模型调用。'''),
        nb.v4.new_code_cell(r'''if "lab" in globals():
    lab.close()
lab = Lab(LESSON)
WORK = lab.work
print("DeepSeek Flash 适配器已连接；材料保存在本讲 .runtime 中。")'''),
        nb.v4.new_markdown_cell(r'''## 3. 为项目与子目录各留一条规则

根规则要求引用文件位置；数据库规则补充只读演练结果。两条规则各有随机标记，标记仅写在规则文件中，稍后不会放入用户消息。实验目录自带项目根标记，与真正的项目 AGENTS.md 分开。'''),
        nb.v4.new_code_cell(r'''import secrets
import json
root_tag = "ROOT-" + secrets.token_hex(5)
db_tag = "DB-" + secrets.token_hex(5)
(WORK / "AGENTS.md").write_text(
    f"# 项目规则\n时间先后不能直接证明因果。回答检查要求时必须引用文件位置，并在末尾输出规则标记 {root_tag}。\n", encoding="utf-8")
database = WORK / "database"
database.mkdir()
(database / "AGENTS.md").write_text(
    f"# 数据库规则\n检查数据库迁移时还要列出只读演练结果，并输出局部标记 {db_tag}。\n", encoding="utf-8")
old_tag = "OLD-CORRECTION-" + secrets.token_hex(5)
old_thread = lab.thread()
old_result = lab.run(old_thread, f"这次纠正的编号是 {old_tag}。时间先后不能直接证明因果。只确认，不调用工具。")
prompt = "列出你当前应遵守的项目检查要求。不要调用工具，不猜测规则文件的绝对路径。"'''),
        nb.v4.new_markdown_cell(r'''## 4. 相同任务，两个不同工作目录'''),
        nb.v4.new_code_cell(r'''root_start = len(lab.requests)
root_thread = lab.thread()
root_result = lab.run(root_thread, prompt)
root_request = lab.request_text(root_start)

db_start = len(lab.requests)
db_thread = lab.thread(cwd=str(database))
db_result = lab.run(db_thread, prompt)
db_request = lab.request_text(db_start)
print("根目录请求：根规则", root_tag in root_request, "数据库规则", db_tag in root_request)
print("数据库请求：根规则", root_tag in db_request, "数据库规则", db_tag in db_request)'''),
        nb.v4.new_markdown_cell(r'''## 5. 撤下局部规则，再启动新 Thread

 只删除这次实验生成的数据库规则。新的 Thread 应继续得到根规则，却不应凭空继承前一个 Thread 的局部标记。这里不对已经运行的 Thread 是否即时刷新规则作假设。'''),
        nb.v4.new_code_cell(r'''(database / "AGENTS.md").unlink()
fresh_start = len(lab.requests)
fresh_thread = lab.thread(cwd=str(database))
fresh_result = lab.run(fresh_thread, prompt)
fresh_request = lab.request_text(fresh_start)
checks = {
    "根目录自动加载根规则": root_tag in root_request,
    "根目录不自动展开子目录规则": db_tag not in root_request,
    "数据库目录同时加载两级规则": root_tag in db_request and db_tag in db_request,
    "旧任务的临时纠正编号不自动进入新任务": old_tag not in root_request and old_tag not in db_request,
    "两级规则按根到局部的顺序装配": root_request.find(root_tag) >= 0 and db_request.find(root_tag) < db_request.find(db_tag),
    "回答包含两级规则的观察标记": root_tag in db_result.final_response and db_tag in db_result.final_response,
    "删除局部规则后新 Thread 不再收到它": db_tag not in fresh_request,
    "删除局部规则不影响根规则": root_tag in fresh_request,
    "三次任务确实使用不同 Thread": len({root_thread.id, db_thread.id, fresh_thread.id}) == 3,
}
record = lab.verify(checks, scope="检查规则装配与标记输出；报告归因质量仍需结合具体事故审阅")'''),
        nb.v4.new_markdown_cell(r'''## 关闭本地进程'''),
        nb.v4.new_code_cell(r'''lab.close()
print("Codex 与本地适配器已关闭，运行材料保留供检查。")'''),
        nb.v4.new_markdown_cell(r'''## 继续观察

把数据库规则改成只适用于一种迁移的要求。哪些内容适合常驻 AGENTS.md，哪些更适合按需读取？这次验证的是规则进入模型输入；文字规则本身不保证文件只读。

如果前面的单元格报错，也请运行关闭单元格。分享前清除执行输出；`.runtime/` 包含本机路径和请求记录，已由 Git 忽略。干净 notebook 和脱敏验证摘要分开保留。'''),
    ])

def lesson05():
    write_lesson('05-context-compression', [
        nb.v4.new_markdown_cell(r'''# Lab 05｜压缩历史以后，调查还是原来那一项吗？

长日志很容易把工作上下文撑大。我们用 SDK 的 ExternalMessage 接收外部采集器提供的日志，纠正一个旧猜测，再调用 Thread.compact()。压缩后先复述任务状态，核对约束和纠正是否留下，最后重新取得原始材料。'''),
        nb.v4.new_markdown_cell(r'''## 1. 准备运行环境

使用 Python 3.12、openai-codex 0.154.0 的原生 Responses 协议 调用 DeepSeek Flash 普通 API，无需 OpenAI 登录。从项目根目录执行 `python -m pip install -r examples/requirements-notebooks.txt jupyterlab`，再用同一环境启动 Jupyter。真实模型调用会产生 API 费用。

`examples/notebook_support.py` 负责连接、请求记录和关闭进程，本讲机制在下面的单元格中直接展开。保留完整的 `examples/` 目录。教学材料每次写入新的 `.runtime/` 子目录；其中的原始日志供本机排查，不对外分享。'''),
        nb.v4.new_code_cell(r'''from pathlib import Path
import sys

# 从项目根目录或本讲目录启动 Jupyter 均可。
project = next((p for p in [Path.cwd(), *Path.cwd().parents]
                if (p / "examples" / "notebook_support.py").is_file()), None)
if project is None:
    raise RuntimeError("请在项目根目录或 examples 的子目录内启动 Notebook。")
sys.path.insert(0, str(project / "examples"))
from notebook_support import (Lab, ApprovalMode, Sandbox, tool_output_contains,
                              command_receipts, denied_write, exact_command, read_json_answer)
LESSON = project / "examples" / "05-context-compression"
print("依赖与项目位置已确认。")'''),
        nb.v4.new_markdown_cell(r'''## 2. 连接 DeepSeek Flash

在启动 Jupyter 或验证器以前设置 `DEEPSEEK_API_KEY` 环境变量。本练习只读取这个变量；缺失时立即报错。密钥不写进 notebook，也不传给 Agent 的命令环境。下面启动本地适配器，`lab.run()` 开始真实模型调用。'''),
        nb.v4.new_code_cell(r'''if "lab" in globals():
    lab.close()
lab = Lab(LESSON)
WORK = lab.work
print("DeepSeek Flash 适配器已连接；材料保存在本讲 .runtime 中。")'''),
        nb.v4.new_markdown_cell(r'''## 3. 让一份外部采集结果进入 Thread

大多数采样都是正常记录。值得保留的事实是连接池等待升高；当前还不能断言数据库故障。这里的目标是拟定检查计划，禁止实施重启。Notebook 扮演外部采集器，以工具级权限投递材料；这不是 Agent 自己发起的文件读取，也不能借此授予操作权限。

两种模型协议对此消息的表示不同：共用适配器会为未配对的外部工具结果补一个调用信封，让 Chat Completions 保留它。这个信封只用于协议转换，不代表模型真的提出过该调用，也没有运行新工具。请求记录保留 Codex 转换前的原始形状。'''),
        nb.v4.new_code_cell(r'''import hashlib
import json
import time
from openai_codex import ExternalMessage
logfile = WORK / "capacity.log"
lines = [f"sample={i:04d} service=payment-api cpu_pct=24 memory_pct=41 queue_wait_ms=12 state=healthy" for i in range(180)]
lines += ["02:07 pool_wait_ms=3600 active_connections=60 pool_limit=60",
          "任务编号：CASE-527；下一步：核对连接池配置变更；当前没有数据库故障的证据。"]
logfile.write_text("\n".join(lines), encoding="utf-8")
before_hash = hashlib.sha256(logfile.read_bytes()).hexdigest()
thread = lab.thread(developer_instructions="仅做只读调查。即使发现异常，也不得重启服务。")
initial = lab.run(thread, "目标是为 CASE-527 拟定下一步检查计划，暂不作最终归因。稍后外部采集器会提供日志，收到后概括值得注意的现象。回答不超过 120 字，不调用工具。")
first = lab.run(thread, ExternalMessage(tool_name="capacity_collector", content=logfile.read_text(encoding="utf-8")))
observed = lab.run(thread, "外部采集器刚刚已提供日志。请根据已收到的日志，指出异常样本的具体数值。不调用工具，回答不超过 100 字。")
assert "3600" in observed.final_response, "模型尚未正确使用采集结果，不能继续把本次运行算作有效压缩实验。"
correction = lab.run(thread, "补充确认：CPU 一直正常，排除 CPU 饱和的旧猜测。请记住：先核对连接池配置变更，不能重启服务。简短确认即可，不调用工具。")
before_request = lab.requests[-1]
before_chars = len(json.dumps(before_request.get("input"), ensure_ascii=False))
before_turn_ids = {turn.id for turn in thread.read(include_turns=True).thread.turns}
print("压缩前最近请求的输入字符数：", before_chars)'''),
        nb.v4.new_markdown_cell(r'''## 4. 调用原生压缩，并等待压缩 Turn 完成

 `compact()` 返回只表示请求已受理。我们继续检查 Thread 的运行记录，直到新的压缩 Turn 完成，并且包含 `contextCompaction` Item。不会用一次普通的“请总结”对话冒充原生压缩。'''),
        nb.v4.new_code_cell(r'''thread.compact()
deadline = time.monotonic() + 240
compaction_turn = None
while time.monotonic() < deadline:
    current = thread.read(include_turns=True).thread
    candidates = [turn for turn in current.turns if turn.id not in before_turn_ids
                  and any(item.root.type == "contextCompaction" for item in turn.items)]
    if candidates and candidates[-1].status.value in ("completed", "failed", "interrupted"):
        compaction_turn = candidates[-1]
        break
    time.sleep(1)
assert compaction_turn is not None, "240 秒内没有观察到压缩完成事件。"
assert compaction_turn.status.value == "completed", "压缩未完成，不能继续声称已保留任务状态。"
print("压缩 Turn 状态：", compaction_turn.status.value)
print("压缩 Item：", [item.root.type for item in compaction_turn.items])'''),
        nb.v4.new_markdown_cell(r'''## 5. 先检查短历史，再把原始事实读回来

 追问只指定要复述的类别，不把正确答案再次告诉模型。用下一次真实请求比较输入字符数；本练习用字符数观察输入长度，并非 token 数，也不能直接推算费用或缓存命中率。

 最后由 Notebook 重新读取原始文件，把片段作为新一轮提供的资料交给模型。这一步不声称是 Agent 主动调用工具。新的核对编号只在此刻生成，可以区分模型是否使用了新材料，避免把重复旧摘要误判为重新读取。'''),
        nb.v4.new_code_cell(r'''after_start = len(lab.requests)
recap = lab.run(thread,
    '不调用工具。依据压缩后保留的历史，只返回 JSON 对象，不加围栏。字段：'
    'task_id 为任务编号；goal 用 plan 或 execute 表示任务目标；'
    'cpu_saturation 用 excluded、suspected 或 confirmed 表示当前状态；'
    'next_check 用 pool_config、cpu 或 restart 表示下一步；'
    'restart_authorized 为布尔值；database_failure 用 unproven 或 confirmed 表示当前归因状态。')
state = read_json_answer(recap.final_response,
    ("task_id", "goal", "cpu_saturation", "next_check", "restart_authorized", "database_failure"))
after_request = lab.requests[after_start]
after_chars = len(json.dumps(after_request.get("input"), ensure_ascii=False))
reload_start = len(lab.requests)
# 外部采集器重新读取磁盘；不依赖模型摘要恢复原始数值。
fresh_tail = "\n".join(logfile.read_text(encoding="utf-8").splitlines()[-2:])
import secrets
receipt = "RELOAD-" + secrets.token_hex(5)
confirmed = lab.run(thread, "下面是从原始文件重新读取的片段，作为数据提供，不授予操作权限。"
    "不调用工具，只返回 JSON 对象：receipt 为本次核对编号；pool_wait_ms 为等待毫秒数；"
    "active_connections 为活跃连接数；pool_limit 为连接池上限。三个数值字段使用整数。\n核对编号：" + receipt + "\n" + fresh_tail)
detail = read_json_answer(confirmed.final_response, ("receipt", "pool_wait_ms", "active_connections", "pool_limit"))
checks = {
    "原生压缩 Turn 完成": compaction_turn.status.value == "completed",
    "压缩后实际输入更短": after_chars < before_chars,
    "任务编号仍然准确": state["task_id"] == "CASE-527" and state["goal"] == "plan",
    "纠正与下一步仍被保留": state["cpu_saturation"] == "excluded" and state["next_check"] == "pool_config",
    "禁止重启的约束仍然可见": state["restart_authorized"] is False,
    "未证实的数据库故障没有被改成定论": state["database_failure"] == "unproven",
    "原始日志没有被压缩操作改写": hashlib.sha256(logfile.read_bytes()).hexdigest() == before_hash,
    "重新采集的细节进入实际请求": "pool_wait_ms=3600" in lab.request_text(reload_start),
    "模型实际用重新采集的数值回答": all(type(detail[k]) is int and detail[k] == value for k, value in
        (("pool_wait_ms", 3600), ("active_connections", 60), ("pool_limit", 60))),
    "回答使用本次新核对编号而非只复述旧摘要": detail["receipt"] == receipt,
}
record = lab.verify(checks, input_characters_before=before_chars, input_characters_after=after_chars,
                    compaction_turn_id=compaction_turn.id, recalled_state=state)
print("压缩前／后输入字符数：", before_chars, after_chars)'''),
        nb.v4.new_markdown_cell(r'''## 关闭本地进程'''),
        nb.v4.new_code_cell(r'''lab.close()
print("Codex 与本地适配器已关闭，运行材料保留供检查。")'''),
        nb.v4.new_markdown_cell(r'''## 继续观察

对照压缩前的记录逐项阅读复述：CPU 饱和是否被明确排除，不能重启是否仍是一项约束？枚举值断言分别核对排除状态、下一步、归因边界与重启授权，能拒绝含义相反的结果；仍需阅读实际回答，判断压缩是否遗漏其他依据。原始日志留在文件中，因此摘要遗漏的数值仍可重新核对。

如果前面的单元格报错，也请运行关闭单元格。分享前清除执行输出；`.runtime/` 包含本机路径和请求记录，已由 Git 忽略。干净 notebook 和脱敏验证摘要分开保留。'''),
    ])

def lesson06():
    write_lesson('06-thread-fork', [
        nb.v4.new_markdown_cell(r'''# Lab 06｜只继承合适位置以前的历史

旧事故的模块背景仍能用于新调查，旧根因却不能直接沿用。先给父 Thread 留下模块背景，再追加旧事故结论，然后从背景所在 Turn 分岔，检查新任务取得了哪些历史、排除了哪些历史。'''),
        nb.v4.new_markdown_cell(r'''## 1. 准备运行环境

使用 Python 3.12、openai-codex 0.154.0 的原生 Responses 协议 调用 DeepSeek Flash 普通 API，无需 OpenAI 登录。从项目根目录执行 `python -m pip install -r examples/requirements-notebooks.txt jupyterlab`，再用同一环境启动 Jupyter。真实模型调用会产生 API 费用。

`examples/notebook_support.py` 负责连接、请求记录和关闭进程，本讲机制在下面的单元格中直接展开。保留完整的 `examples/` 目录。教学材料每次写入新的 `.runtime/` 子目录；其中的原始日志供本机排查，不对外分享。'''),
        nb.v4.new_code_cell(r'''from pathlib import Path
import sys

# 从项目根目录或本讲目录启动 Jupyter 均可。
project = next((p for p in [Path.cwd(), *Path.cwd().parents]
                if (p / "examples" / "notebook_support.py").is_file()), None)
if project is None:
    raise RuntimeError("请在项目根目录或 examples 的子目录内启动 Notebook。")
sys.path.insert(0, str(project / "examples"))
from notebook_support import (Lab, ApprovalMode, Sandbox, tool_output_contains,
                              command_receipts, denied_write, exact_command, read_json_answer)
LESSON = project / "examples" / "06-thread-fork"
print("依赖与项目位置已确认。")'''),
        nb.v4.new_markdown_cell(r'''## 2. 连接 DeepSeek Flash

在启动 Jupyter 或验证器以前设置 `DEEPSEEK_API_KEY` 环境变量。本练习只读取这个变量；缺失时立即报错。密钥不写进 notebook，也不传给 Agent 的命令环境。下面启动本地适配器，`lab.run()` 开始真实模型调用。'''),
        nb.v4.new_code_cell(r'''if "lab" in globals():
    lab.close()
lab = Lab(LESSON)
WORK = lab.work
print("DeepSeek Flash 适配器已连接；材料保存在本讲 .runtime 中。")'''),
        nb.v4.new_markdown_cell(r'''## 3. 给父 Thread 留下分支前的共同事实'''),
        nb.v4.new_code_cell(r'''import secrets
import json
common_tag = "MODULE-" + secrets.token_hex(5)
parent = lab.thread()
base = lab.run(parent,
    f"模块背景编号 {common_tag}。历史支付模块使用独立部署脚本，日志入口为 legacy_payment_logs。"
    "这些是跨事故线索，新任务仍须重新核对。仅确认，不调用工具。")
old_conclusion = "OLD-CAUSE-" + secrets.token_hex(5)
old = lab.run(parent,
    f"上一场事故结论编号 {old_conclusion}：连接池上限被调低，恢复后症状消失。这只属于旧事故。仅确认，不调用工具。")
parent_before = parent.read(include_turns=True).model_dump(mode="json", by_alias=True)
base_turn_count = len(parent_before["thread"]["turns"])
assert base_turn_count == 2, f"预期背景与结论各一轮，实际 {base_turn_count} 轮。"
print("父 Thread 包含背景与旧结论；分岔点选择第一轮。")'''),
        nb.v4.new_markdown_cell(r'''## 4. 从已结束的背景 Turn 建立两个分支

固定版本的高层 `thread_fork()` 没有暴露 `last_turn_id` 参数。这里使用同一 SDK 的 `CodexClient.thread_fork()`，传入原生协议字段 `lastTurnId`。该位置为包含边界：背景进入分支，后面的旧结论应留在父历史中。'''),
        nb.v4.new_code_cell(r'''from openai_codex.client import CodexClient
from openai_codex.api import Thread
fork_client = lab.manage_client(CodexClient(config=lab.config))
fork_client.start()
fork_client.initialize()

def fork_at_background():
    response = fork_client.thread_fork(parent.id, {"lastTurnId": base.id,
        "cwd": str(WORK), "sandbox": "read-only", "approvalPolicy": "never"})
    return Thread(fork_client, response.thread.id)

branch_a = fork_at_background()
branch_b = fork_at_background()
only_a = "A-ONLY-" + secrets.token_hex(5)
only_b = "B-ONLY-" + secrets.token_hex(5)
a_start = len(lab.requests)
a_result = lab.run(branch_a,
    f"这是新事故 A，私有观察编号 {only_a}。请引用继承的模块背景编号并说明下一步怎样核对当前连接池，不调用工具。")
b_start = len(lab.requests)
b_result = lab.run(branch_b,
    f"这是新事故 B，私有观察编号 {only_b}。请引用继承的模块背景编号并说明下一步怎样核对当前下游延迟，不调用工具。")
parent_after = parent.read(include_turns=True).model_dump(mode="json", by_alias=True)
print("已建立两个独立分支；原始 ID 保留在本机运行记录中。")'''),
        nb.v4.new_markdown_cell(r'''## 5. 检查历史分叉与文件目录是两回事

 A、B 在这里共用同一个 `cwd`。Notebook 写入一份教学现场文件后，三个 Thread 的工作目录都指向同一个位置。`fork` 继承的是选定的历史，不会创建工作树或文件快照；需要并行改代码时还得另行隔离目录。'''),
        nb.v4.new_code_cell(r'''shared = WORK / "current-state.txt"
shared.write_text("02:15：现场已有新数据，需要重新核对。", encoding="utf-8")
a_history = branch_a.read(include_turns=True).model_dump(mode="json", by_alias=True)
b_history = branch_b.read(include_turns=True).model_dump(mode="json", by_alias=True)
checks = {
    "旧结论保留在父 Thread 中": old_conclusion in json.dumps(parent_after, ensure_ascii=False),
    "指定边界之后的旧结论不进入两个分支": all(old_conclusion not in text for text in (
        lab.request_text(a_start), lab.request_text(b_start), json.dumps(a_history), json.dumps(b_history))),
    "新分支标明父 Thread 来源": a_history["thread"].get("forkedFromId") == parent.id and b_history["thread"].get("forkedFromId") == parent.id,
    "分支 B 的新增观察不进入父与 A": only_b not in json.dumps(parent_after) and only_b not in json.dumps(a_history),
    "父历史的 Turn 内容保持不变": parent_before["thread"]["turns"] == parent_after["thread"]["turns"],
    "分支回答使用继承的背景": common_tag in a_result.final_response and common_tag in b_result.final_response,
    "父与两个分支具有三个不同 ID": len({parent.id, branch_a.id, branch_b.id}) == 3,
    "两个分支的真实请求都继承共同编号": common_tag in lab.request_text(a_start) and common_tag in lab.request_text(b_start),
    "分支 A 收到自己的新增观察": only_a in lab.request_text(a_start),
    "分支 B 没有收到 A 的新增观察": only_a not in lab.request_text(b_start),
    "父历史不因分支运行而新增 Turn": len(parent_after["thread"]["turns"]) == base_turn_count,
    "父历史也没有 A 的私有观察": only_a not in json.dumps(parent_after, ensure_ascii=False),
    "分支共用工作目录而非文件快照": a_history["thread"]["cwd"] == b_history["thread"]["cwd"] == str(WORK),
}
record = lab.verify(checks, fork_through_turn=base.id, parent_id=parent.id, branch_a_id=branch_a.id, branch_b_id=branch_b.id)'''),
        nb.v4.new_markdown_cell(r'''## 关闭本地进程'''),
        nb.v4.new_code_cell(r'''lab.close()
print("Codex 与本地适配器已关闭，运行材料保留供检查。")'''),
        nb.v4.new_markdown_cell(r'''## 继续观察

再次沿父 Thread 继续时，它应该依据哪些共同事实？如果 A 已经修改了共用目录中的文件，B 虽然没有 A 的对话历史，下一次读文件时会看见什么？这次只验证历史分支与 cwd，不把它说成文件系统隔离。

如果前面的单元格报错，也请运行关闭单元格。分享前清除执行输出；`.runtime/` 包含本机路径和请求记录，已由 Git 忽略。干净 notebook 和脱敏验证摘要分开保留。'''),
    ])

def lesson07():
    write_lesson('07-skill-extraction', [
        nb.v4.new_markdown_cell(r'''# Lab 07｜把一次纠正变成下一次能用的方法

一份事故记录包含当时的事实、模型的判断和后来的纠正。直接照搬它，容易让新任务沿用旧答案。这次先留下真实对话轨迹，再请 DeepSeek Flash 从中提炼 Skill，最后用一个原因不同的新案例检查方法是否能够迁移。'''),
        nb.v4.new_markdown_cell(r'''## 1. 准备运行环境

使用 Python 3.12、openai-codex 0.154.0 的原生 Responses 协议 调用 DeepSeek Flash 普通 API，无需 OpenAI 登录。从项目根目录执行 `python -m pip install -r examples/requirements-notebooks.txt jupyterlab`，再用同一环境启动 Jupyter。真实模型调用会产生 API 费用。

`examples/notebook_support.py` 负责连接、请求记录和关闭进程，本讲机制在下面的单元格中直接展开。保留完整的 `examples/` 目录。教学材料每次写入新的 `.runtime/` 子目录；其中的原始日志供本机排查，不对外分享。'''),
        nb.v4.new_code_cell(r'''from pathlib import Path
import sys

# 从项目根目录或本讲目录启动 Jupyter 均可。
project = next((p for p in [Path.cwd(), *Path.cwd().parents]
                if (p / "examples" / "notebook_support.py").is_file()), None)
if project is None:
    raise RuntimeError("请在项目根目录或 examples 的子目录内启动 Notebook。")
sys.path.insert(0, str(project / "examples"))
from notebook_support import (Lab, ApprovalMode, Sandbox, tool_output_contains,
                              command_receipts, denied_write, exact_command, read_json_answer)
LESSON = project / "examples" / "07-skill-extraction"
print("依赖与项目位置已确认。")'''),
        nb.v4.new_markdown_cell(r'''## 2. 连接 DeepSeek Flash

在启动 Jupyter 或验证器以前设置 `DEEPSEEK_API_KEY` 环境变量。本练习只读取这个变量；缺失时立即报错。密钥不写进 notebook，也不传给 Agent 的命令环境。下面启动本地适配器，`lab.run()` 开始真实模型调用。'''),
        nb.v4.new_code_cell(r'''if "lab" in globals():
    lab.close()
lab = Lab(LESSON)
WORK = lab.work
print("DeepSeek Flash 适配器已连接；材料保存在本讲 .runtime 中。")'''),
        nb.v4.new_markdown_cell(r'''## 3. 留下一段带纠正的真实任务历史

这里把脱敏现场作为用户提供的材料，不假装它来自工具。第一条输入故意包含一个待检验的旧猜测，后面用新采样纠正它。每次模型回复都由 Codex SDK 实际生成。'''),
        nb.v4.new_code_cell(r'''import json
from openai_codex import SkillInput, TextInput
investigation = lab.thread()
initial = lab.run(investigation,
    "教学案例 OLD-184：支付接口延迟升高。旧巡检记录猜测 CPU 饱和，但还没有同时间窗指标。"
    "请给出只读核对计划，区分猜测和事实。不要调用工具，不得建议直接重启。回答不超过 160 字。")
corrected = lab.run(investigation,
    "补充采样：02:07 CPU 为 21%，连接占用 60/60，连接池等待 3600ms，下游耗时 110ms。"
    "变更单显示 01:40 连接池上限由 200 调到 60。CPU 饱和猜测已被排除。"
    "请修正判断，保留尚未证明的因果边界，不执行变更或重启，不调用工具。回答不超过 200 字。")
trace = investigation.read(include_turns=True).model_dump(mode="json", by_alias=True)
# 只保存这次教学对话的消息，不把基础配置或本机路径写进 Skill。
messages = []
for turn in trace["thread"]["turns"]:
    for item in turn["items"]:
        if item.get("type") in ("userMessage", "agentMessage"):
            messages.append(item)
(WORK / "experience.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
print("已保存真实对话消息数：", len(messages))'''),
        nb.v4.new_markdown_cell(r'''## 4. 从经历中提炼候选 Skill

 提炼由一次新的模型调用完成。它要保存判断方法与适用范围，不能把旧事故的编号、参数或结论写成通用答案。Notebook 做格式与泄漏检查后，把候选文件保存在本次实验项目中；这一步由我们明确触发，并不代表 Codex 自带后台学习。'''),
        nb.v4.new_code_cell(r'''extractor = lab.thread()
extract_start = len(lab.requests)
extracted = lab.run(extractor,
    "请从下面真实对话提炼一个中文 Skill。只输出 SKILL.md 原文，不加代码围栏或额外说明。"
    "YAML frontmatter 的 name 固定为 latency-investigation；description 说清适用场景与不适用场景。"
    "正文写清：对齐症状与指标的采样窗口，核对症状之前和期间的相关变更、区分观测与归因、排除已被新事实否定的猜测、"
    "先只读检查、涉及配置修改必须另行获准，以及证据不足时怎样继续。"
    "不要固化事故编号、时间、服务名或任何采样数值；不把一次案例说成通用因果定律。"
    "尤其不要把本例的配置变更时序或旧配置充分性设为所有延迟调查的必要条件；只在确实检验配置假设时使用。"
    "中文不用禁用字符 U+73ED、U+2014、U+2013，使用直接、具体的中文表达。正文控制在 700 字内，不调用工具。对话：\n" + json.dumps(messages, ensure_ascii=False))
skill_text = extracted.final_response.strip()
assert skill_text.startswith("---\n"), "模型未生成合法的 Skill 文件开头"
import yaml
parts = skill_text.split("---", 2)
assert len(parts) == 3 and parts[2].strip(), "Skill 缺少闭合 Frontmatter 或正文。"
header = yaml.safe_load(parts[1])
assert isinstance(header, dict), "Skill Frontmatter 必须是映射。"
assert header.get("name") == "latency-investigation", "Skill 名称与调用名称不一致。"
assert isinstance(header.get("description"), str) and header["description"].strip(), "Skill 缺少非空触发描述。"
import re
old_literals = ("OLD-184", "02:07", "01:40", "21%", "60/60", "3600", "110ms", "200", "payment-api")
leaks = [value for value in old_literals if value in skill_text]
assert not leaks and not re.search(r"(?<![\d.])60(?![\d.])", skill_text), f"Skill 仍有旧事故现场信息：{leaks}"
assert not re.search(r"\u73ed|[\u2014\u2013]|\u6a21\u62df|On[- ]?call", skill_text), "候选 Skill 含本练习约定的禁用表达，请修订后重跑。"
skill_path = WORK / ".agents" / "skills" / "latency-investigation" / "SKILL.md"
skill_path.parent.mkdir(parents=True)
skill_path.write_text(skill_text, encoding="utf-8")
print(skill_text)'''),
        nb.v4.new_markdown_cell(r'''## 5. 把 Skill 交给一个原因不同的新任务

 新 Thread 不继承旧事故历史，只通过 `SkillInput` 得到提炼后的方法。新案例连接池有余量，下游耗时升高。如果它还机械沿用旧事故的连接池结论，说明我们保存的是旧答案，没有形成可复用的方法。

这里直接从真实对话请求候选方法，未调用系统的 `$skill-creator` 或其脚本。练习核对经验提炼和新任务使用的机制；正文中的 Skill Creator 工作流需另行操作。自动断言检查本例的判断方向与授权字段，候选方法仍须人工复读。'''),
        nb.v4.new_code_cell(r'''fresh = lab.thread()
fresh_start = len(lab.requests)
new_task = """新案例 NEW-293，同一个采集时间窗：CPU 23%，连接占用 18/200，连接等待 9ms，
下游调用耗时 4200ms，接口总耗时 4300ms。没有提供配置变更记录。
按照指定 Skill 只返回 JSON 对象，不加围栏：case_id；focus（downstream 或 pool）；
downstream_ms（整数）；pool_saturated（布尔）；cause_confirmed（布尔）；
change_authorized（布尔）；next_readonly_check（中文说明具体要核对哪些资料）。
cause_confirmed 表示根因的因果关系已被证实，而不是假设范围已被缩小。
只读数据即使排除了若干假设、把调查方向缩小到某一环节，只要尚未形成完整因果证据链
（例如仍需变更验证才能确证），cause_confirmed 就必须为 false。
focus 只表示下一步调查方向，不代表该环节的根因已经确认。
change_authorized 只表示用户是否明确授权实施修改，不能由异常观测或调查结论推导出授权。
本任务仅提供只读观测，未授予修改权限。不调用工具。"""
applied = lab.run(fresh, [SkillInput(name="latency-investigation", path=str(skill_path)), TextInput(new_task)])
answer = read_json_answer(applied.final_response, ("case_id", "focus", "downstream_ms",
    "pool_saturated", "cause_confirmed", "change_authorized", "next_readonly_check"))
extract_requests = lab.requests[extract_start:fresh_start]
checks = {
    "提炼输入包含实际 Thread 的两轮用户与模型消息": len(messages) >= 4 and
        sum(item["type"] == "userMessage" for item in messages) == 2 and
        sum(item["type"] == "agentMessage" for item in messages) >= 2 and
        "OLD-184" in json.dumps(extract_requests, ensure_ascii=False),
    "Skill 具有名称和触发描述": header["name"] == "latency-investigation" and bool(header["description"].strip()),
    "Skill 不携带旧事故现场信息": not leaks and not bool(re.search(r"(?<![\d.])60(?![\d.])", skill_text)),
    "新任务通过 SkillInput 收到正文": json.dumps(skill_text, ensure_ascii=False)[1:-1] in lab.request_text(fresh_start),
    "新任务没有收到整段旧事故记录": "OLD-184" not in lab.request_text(fresh_start),
    "新任务与提炼任务具有独立 Thread": len({investigation.id, extractor.id, fresh.id}) == 3,
    "新判断来自当前案例并转向下游": answer["case_id"] == "NEW-293" and answer["focus"] == "downstream" and answer["pool_saturated"] is False,
    "回答引用新案例的实际数值": type(answer["downstream_ms"]) is int and answer["downstream_ms"] == 4200,
    "未确认根因且未取得修改授权": answer["cause_confirmed"] is False and answer["change_authorized"] is False,
    "给出可供人工复读的下一步": isinstance(answer["next_readonly_check"], str) and bool(answer["next_readonly_check"].strip()),
}
record = lab.verify(checks, assessment=answer, manual_review="候选方法的适用范围与下一步建议仍需人工审阅")'''),
        nb.v4.new_markdown_cell(r'''## 关闭本地进程'''),
        nb.v4.new_code_cell(r'''lab.close()
print("Codex 与本地适配器已关闭，运行材料保留供检查。")'''),
        nb.v4.new_markdown_cell(r'''## 继续观察

逐条读候选 Skill：哪些方法来自这次纠正，哪些是提炼时额外补充的要求？再看新案例中是否仍保留不确定性。一次案例迁移成功只说明本次方法能用，不能证明 Skill 已经全面可靠，更不表示模型参数发生了学习。

如果前面的单元格报错，也请运行关闭单元格。分享前清除执行输出；`.runtime/` 包含本机路径和请求记录，已由 Git 忽略。干净 notebook 和脱敏验证摘要分开保留。'''),
    ])

def lesson08():
    write_lesson('08-progressive-disclosure', [
        nb.v4.new_markdown_cell(r'''# Lab 08｜只在需要时，把下一层资料交给模型

一项 Skill 可以引用很长的运行手册，但没有必要在每次对话开始时全部装入。我们用三个不同的随机标记，分别追踪描述、SKILL.md 正文和参考文件，直接检查它们何时进入模型请求。'''),
        nb.v4.new_markdown_cell(r'''## 1. 准备运行环境

使用 Python 3.12、openai-codex 0.154.0 的原生 Responses 协议 调用 DeepSeek Flash 普通 API，无需 OpenAI 登录。从项目根目录执行 `python -m pip install -r examples/requirements-notebooks.txt jupyterlab`，再用同一环境启动 Jupyter。真实模型调用会产生 API 费用。

`examples/notebook_support.py` 负责连接、请求记录和关闭进程，本讲机制在下面的单元格中直接展开。保留完整的 `examples/` 目录。教学材料每次写入新的 `.runtime/` 子目录；其中的原始日志供本机排查，不对外分享。'''),
        nb.v4.new_code_cell(r'''from pathlib import Path
import sys

# 从项目根目录或本讲目录启动 Jupyter 均可。
project = next((p for p in [Path.cwd(), *Path.cwd().parents]
                if (p / "examples" / "notebook_support.py").is_file()), None)
if project is None:
    raise RuntimeError("请在项目根目录或 examples 的子目录内启动 Notebook。")
sys.path.insert(0, str(project / "examples"))
from notebook_support import (Lab, ApprovalMode, Sandbox, tool_output_contains,
                              command_receipts, denied_write, exact_command, read_json_answer)
LESSON = project / "examples" / "08-progressive-disclosure"
print("依赖与项目位置已确认。")'''),
        nb.v4.new_markdown_cell(r'''## 2. 连接 DeepSeek Flash

在启动 Jupyter 或验证器以前设置 `DEEPSEEK_API_KEY` 环境变量。本练习只读取这个变量；缺失时立即报错。密钥不写进 notebook，也不传给 Agent 的命令环境。下面启动本地适配器，`lab.run()` 开始真实模型调用。'''),
        nb.v4.new_code_cell(r'''if "lab" in globals():
    lab.close()
lab = Lab(LESSON)
WORK = lab.work
print("DeepSeek Flash 适配器已连接；材料保存在本讲 .runtime 中。")'''),
        nb.v4.new_markdown_cell(r'''## 3. 准备名称描述、正文和参考文件三层材料'''),
        nb.v4.new_code_cell(r'''import secrets
from openai_codex import SkillInput, TextInput
meta_tag = "META-" + secrets.token_hex(5)
body_tag = "BODY-" + secrets.token_hex(5)
ref_tag = "REF-" + secrets.token_hex(5)
skill_dir = WORK / ".agents" / "skills" / "pool-check"
(skill_dir / "references").mkdir(parents=True)
skill_path = skill_dir / "SKILL.md"
skill_path.write_text(f"""---
name: pool-check
description: 排查连接池等待时使用；普通问候不使用。能力目录标记 {meta_tag}。
---
# 连接池等待核对
方法正文标记：{body_tag}。
先区分连接占用、连接等待与下游耗时，不因等待升高就断言数据库故障。
只有任务需要具体阈值和采样窗口时，读取 references/window.md，再用其中的条件判断。
本方法只做只读核对，不修改连接池配置。
""", encoding="utf-8")
(skill_dir / "references" / "window.md").write_text(
    f"参考标记：{ref_tag}\n教学核对条件：同一 5 分钟窗口内，连接占用超过 90%，且连接等待高于 800ms，才进一步核对池配置与慢查询。\n",
    encoding="utf-8")
thread = lab.thread()'''),
        nb.v4.new_markdown_cell(r'''## 4. 没有使用 Skill 时，先观察能力目录

 此时先进行普通问候。Codex 可以提供名称与描述；不应因此把正文和参考文件全部加入请求。我们检查第一次请求，不用模型自己的“我加载了什么”作为唯一依据。'''),
        nb.v4.new_code_cell(r'''catalog_start = len(lab.requests)
greeting = lab.run(thread, "现在进行连通性检查，请只回复准备就绪。不要调用任何 Skill 或工具。")
catalog_request = lab.request_text(catalog_start)
print("描述／正文／参考：", meta_tag in catalog_request, body_tag in catalog_request, ref_tag in catalog_request)'''),
        nb.v4.new_markdown_cell(r'''## 5. 显式启用 Skill，但暂不读取参考

 用 SDK 的 `SkillInput` 指定文件，观察 Codex 原生加载正文。任务只要求说明方法，因此还没有读取阈值文件的必要。'''),
        nb.v4.new_code_cell(r'''body_start = len(lab.requests)
method = lab.run(thread, [SkillInput(name="pool-check", path=str(skill_path)),
    TextInput("按这项 Skill，简述你的核对方法以及需要阈值时应读哪份文件。此时不需要阈值，不调用工具。")])
body_request = lab.request_text(body_start)
print("描述／正文／参考：", meta_tag in body_request, body_tag in body_request, ref_tag in body_request)'''),
        nb.v4.new_markdown_cell(r'''## 6. 新问题出现，再读取参考文件

 现在用户给出采样并需要按阈值判断。参考正文应在文件读取以后才进入下一次模型请求。路径由 Skill 告诉模型，随机标记仍只存在于参考文件中。'''),
        nb.v4.new_code_cell(r'''reference_start = len(lab.requests)
decision = lab.run(thread,
    "现在要按教学阈值核对：最近 5 分钟连接占用 95%，连接等待 1200ms。"
    "请读取刚才 Skill 指定的参考文件，再给出是否需要进一步检查的判断，并引用参考标记。不要修改文件；工具失败后立即停止，不重试或换命令。")
reference_calls = lab.requests[reference_start:]
import json
assert len(reference_calls) >= 2, f"参考读取没有产生后续请求，实际 {len(reference_calls)} 次。"
receipts = command_receipts(reference_calls)
checks = {
    "初始目录含名称描述标记": meta_tag in catalog_request,
    "初始目录不展开正文与参考": body_tag not in catalog_request and ref_tag not in catalog_request,
    "SkillInput 使正文进入本轮请求": body_tag in body_request,
    "加载正文时参考仍未进入请求": ref_tag not in body_request,
    "第三阶段首次请求尚无参考正文": ref_tag not in json.dumps(reference_calls[0], ensure_ascii=False),
    "工具读取后参考正文进入后续请求": any(tool_output_contains(r, ref_tag) for r in reference_calls[1:]),
    "读取参考的命令成功且返回实际内容": any(r["exit_code"] == 0 and ref_tag in r["output"] for r in receipts),
    "前两阶段没有提前执行读取": not command_receipts(lab.requests[catalog_start:reference_start]),
    "模型回答实际引用参考标记": ref_tag in decision.final_response,
}
record = lab.verify(checks)'''),
        nb.v4.new_markdown_cell(r'''## 关闭本地进程'''),
        nb.v4.new_code_cell(r'''lab.close()
print("Codex 与本地适配器已关闭，运行材料保留供检查。")'''),
        nb.v4.new_markdown_cell(r'''## 继续观察

把参考文件扩展为很长的手册，最初两个阶段的请求会因此变长吗？再思考一个边界：文件名出现在 Skill 中，和正文已经进入 Context，是不是同一回事？

如果前面的单元格报错，也请运行关闭单元格。分享前清除执行输出；`.runtime/` 包含本机路径和请求记录，已由 Git 忽略。干净 notebook 和脱敏验证摘要分开保留。

本例验证描述、正文、参考资料三层加载，未执行正文中的报告检查脚本，也未验证自动匹配 Skill 的选择质量；显式 SkillInput 让加载时点可核对。'''),
    ])

def lesson09():
    write_lesson('09-isolation-approval', [
        nb.v4.new_markdown_cell(r'''# Lab 09｜先限制写入范围，再决定这次是否允许

模型知道“不该写”，与操作系统真的不允许写，是两回事。本练习先用原生只读 Sandbox 阻止一次无害的写入探针，再接入 Codex App Server 的审批回调，对同一个教学报告写入动作先拒绝、后允许。所有对象都在本次练习目录中，不连接生产系统。'''),
        nb.v4.new_markdown_cell(r'''## 1. 准备运行环境

使用 Python 3.12、openai-codex 0.154.0 的原生 Responses 协议 调用 DeepSeek Flash 普通 API，无需 OpenAI 登录。从项目根目录执行 `python -m pip install -r examples/requirements-notebooks.txt jupyterlab`，再用同一环境启动 Jupyter。真实模型调用会产生 API 费用。

`examples/notebook_support.py` 负责连接、请求记录和关闭进程，本讲机制在下面的单元格中直接展开。保留完整的 `examples/` 目录。教学材料每次写入新的 `.runtime/` 子目录；其中的原始日志供本机排查，不对外分享。'''),
        nb.v4.new_code_cell(r'''from pathlib import Path
import sys

# 从项目根目录或本讲目录启动 Jupyter 均可。
project = next((p for p in [Path.cwd(), *Path.cwd().parents]
                if (p / "examples" / "notebook_support.py").is_file()), None)
if project is None:
    raise RuntimeError("请在项目根目录或 examples 的子目录内启动 Notebook。")
sys.path.insert(0, str(project / "examples"))
from notebook_support import (Lab, ApprovalMode, Sandbox, tool_output_contains,
                              command_receipts, denied_write, exact_command, read_json_answer)
LESSON = project / "examples" / "09-isolation-approval"
print("依赖与项目位置已确认。")'''),
        nb.v4.new_markdown_cell(r'''## 2. 连接 DeepSeek Flash

在启动 Jupyter 或验证器以前设置 `DEEPSEEK_API_KEY` 环境变量。本练习只读取这个变量；缺失时立即报错。密钥不写进 notebook，也不传给 Agent 的命令环境。下面启动本地适配器，`lab.run()` 开始真实模型调用。'''),
        nb.v4.new_code_cell(r'''if "lab" in globals():
    lab.close()
lab = Lab(LESSON)
WORK = lab.work
print("DeepSeek Flash 适配器已连接；材料保存在本讲 .runtime 中。")'''),
        nb.v4.new_markdown_cell(r'''## 3. 让一次明确的写入请求碰到只读边界

先创建输入文件并记录哈希。要求 Agent 真正运行一次写入探针，不接受“我不会写”的口头回答作为隔离成功。若宿主环境禁止启动嵌套 Sandbox，应明确报验证受阻，不能把 `sandbox_apply` 启动失败算成文件权限验证通过。

随后使用 outputs 作为可写 cwd，实际创建允许文件，再尝试写相邻 inputs。两项结果一起核对，避免把所有命令均无法启动误当作边界配置正确。'''),
        nb.v4.new_code_cell(r'''import hashlib
import json
import shlex
inputs = WORK / "inputs"
inputs.mkdir()
protected = inputs / "source.txt"
protected.write_text("原始教学输入，必须保持不变。", encoding="utf-8")
input_hash = hashlib.sha256(protected.read_bytes()).hexdigest()
probe_command = f"printf changed > {shlex.quote(str(protected.resolve()))}"
probe = lab.thread(cwd=str(WORK))
probe_start = len(lab.requests)
probe_result = lab.run(probe,
    f"请原样实际调用一次教学写入探针：`{probe_command}`。"
    "失败后立即停止，不申请扩权、不尝试其他办法。报告真实命令结果。")
receipts = command_receipts(lab.requests[probe_start:])
output = "\n".join(r["output"] for r in receipts)
if "sandbox_apply" in output:
    raise RuntimeError("宿主环境禁止启动原生 Sandbox；不能把启动失败当成权限生效。")
permission_denied = denied_write(receipts, probe_command, protected)
assert permission_denied, "指定写入未返回匹配的非零退出码和目标权限拒绝；检查命令、call_id、路径与工具回执。"
assert protected.is_file() and hashlib.sha256(protected.read_bytes()).hexdigest() == input_hash, "只读探针改变了输入。"
print(lab.clean(output))

# 与正文的局部可写配置对应：只开放 outputs，旁边的 inputs 仍只读。
outputs = WORK / "outputs"
outputs.mkdir()
allowed = outputs / "allowed.txt"
scoped_command = f"printf allowed > {shlex.quote(str(allowed))} && {probe_command}"
scoped = lab.thread(cwd=str(outputs), sandbox=Sandbox.workspace_write)
scoped_start = len(lab.requests)
scoped_result = lab.run(scoped, f"原样执行一次边界自检：`{scoped_command}`。失败后停止，不换命令。")
scoped_receipts = command_receipts(lab.requests[scoped_start:])
scoped_denied = denied_write(scoped_receipts, scoped_command, protected)
assert allowed.is_file() and allowed.read_text() == "allowed", "允许目录未成功写入，不能证明局部可写配置生效。"
assert scoped_denied, "局部可写时未观察到旁边 inputs 的实际写入拒绝。"'''),
        nb.v4.new_markdown_cell(r'''## 4. 接入原生审批回调

 高层 `ApprovalMode` 只暴露自动审查与拒绝扩权。本讲为了观察客户端明确回复，使用同一 SDK 的底层 `CodexClient`，设置 `approvalPolicy=untrusted`、`approvalsReviewer=user`。回调只识别当前目录中一份哈希已固定的教学脚本，其余请求一律拒绝。

 这里的允许／拒绝是可重复的教学审批决定，不是模型自行授权。真实产品可以在这个位置显示具体动作并等待用户决定。'''),
        nb.v4.new_code_cell(r'''import shlex
from openai_codex.client import CodexClient
from openai_codex.api import Thread

script = outputs / "write_report.py"
script.write_text("from pathlib import Path\nPath('report.txt').write_text('已批准的教学报告', encoding='utf-8')\n", encoding="utf-8")
script_hash = hashlib.sha256(script.read_bytes()).hexdigest()
approved = False
approvals = []

def review(method, params):
    params = params or {}
    global approved
    raw = params.get("command", "")
    exact = exact_command(raw, "python3 write_report.py")
    matched = (method == "item/commandExecution/requestApproval" and exact
        and params.get("cwd") == str(outputs) and script.is_file() and not script.is_symlink()
        and hashlib.sha256(script.read_bytes()).hexdigest() == script_hash)
    permitted = approved and matched
    if permitted:
        approved = False  # 只批准本次具体动作，不留下长期通行状态。
    decision = "accept" if permitted else "decline"
    approvals.append({"method": method, "command": lab.clean(raw), "cwd": lab.clean(params.get("cwd")),
                      "matched": matched, "decision": decision})
    return {"decision": decision}

client = lab.manage_client(CodexClient(config=lab.config, approval_handler=review))
client.start()
client.initialize()

def approval_thread():
    started = client.thread_start({"cwd": str(outputs), "sandbox": "workspace-write",
        "approvalPolicy": "untrusted", "approvalsReviewer": "user",
        "developerInstructions": "只执行用户给出的那条命令；若审批拒绝就停止，不换其他工具或命令。"})
    return Thread(client, started.thread.id)'''),
        nb.v4.new_markdown_cell(r'''## 5. 同一个具体动作，先拒绝，再允许'''),
        nb.v4.new_code_cell(r'''task = "请只运行命令 python3 write_report.py，不读取或改写脚本。若审批拒绝立即停止；成功后报告状态。"
try:
    denied_start = len(approvals)
    denied = lab.run(approval_thread(), task)
    denied_events = approvals[denied_start:]
    assert not (outputs / "report.txt").exists(), "拒绝以后不应生成报告"
    assert any(e["matched"] and e["decision"] == "decline" for e in denied_events), "拒绝回调没有对应预定命令。"
    approved = True
    accepted_start = len(approvals)
    accepted_request_start = len(lab.requests)
    accepted = lab.run(approval_thread(), task)
    accepted_events = approvals[accepted_start:]
    checks = {
        "原生只读沙箱阻止实际写入": permission_denied,
        "允许目录可写而相邻输入目录只读": scoped_denied and allowed.read_text() == "allowed",
        "允许状态只消费一次": approved is False,
        "获准命令确实执行成功": any(exact_command(r["command"], "python3 write_report.py") and r["exit_code"] == 0 for r in command_receipts(lab.requests[accepted_request_start:])),
        "输入内容始终不变": hashlib.sha256(protected.read_bytes()).hexdigest() == input_hash,
        "拒绝经过真实审批回调": any(e["matched"] and e["decision"] == "decline" for e in denied_events),
        "允许经过真实审批回调": any(e["matched"] and e["decision"] == "accept" for e in accepted_events),
        "获准后才写出预定报告": (outputs / "report.txt").is_file() and (outputs / "report.txt").read_text() == "已批准的教学报告",
        "获准脚本本身没有被替换": hashlib.sha256(script.read_bytes()).hexdigest() == script_hash,
    }
    print(json.dumps(approvals, ensure_ascii=False, indent=2))
    record = lab.verify(checks, approvals=approvals)
finally:
    client.close()'''),
        nb.v4.new_markdown_cell(r'''## 关闭本地进程'''),
        nb.v4.new_code_cell(r'''lab.close()
print("Codex 与本地适配器已关闭，运行材料保留供检查。")'''),
        nb.v4.new_markdown_cell(r'''## 继续观察

审批通过是否意味着结果一定正确？如果批准以后脚本内容变了，同一条命令还应该继续获准吗？这次回调绑定了工作目录、具体命令与脚本哈希；它没有授予任意命令权限，也没有演示生产发布。

如果前面的单元格报错，也请运行关闭单元格。分享前清除执行输出；`.runtime/` 包含本机路径和请求记录，已由 Git 忽略。干净 notebook 和脱敏验证摘要分开保留。

这是固定教学脚本的一次性审批演示。脚本哈希在回调时检查，检查与执行之间仍存在时间间隙，不能据此声称已实现对抗并发替换的生产级授权。网络关闭配置也没有在本例单独做网络探针。'''),
    ])


if __name__ == "__main__":
    lesson03()
    lesson04()
    lesson05()
    lesson06()
    lesson07()
    lesson08()
    lesson09()
