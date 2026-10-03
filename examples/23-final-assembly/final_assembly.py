"""第 23 讲：受控巡检参考实现。

运行方式：
  cd 仓库根
  export GLM_API_KEY=...
  unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
  .deps/hermes-agent/.venv/bin/python examples/23-final-assembly/final_assembly.py

前台是 glm-5.2 与只读 mock 的真实工具循环，未启动完整 Hermes 实例。
候选仅由 evolve_server workflow validated 产生；评测采用后才进入加载目录。
导入本模块不读取密钥、不清理输出、不发起模型请求；执行 main 时才运行流程。
"""

import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

REPO_ROOT = Path(__file__).resolve().parents[2]
HERMES_SRC = Path(
    os.environ.get("HERMES_SRC") or REPO_ROOT / ".deps" / "hermes-agent"
).expanduser().resolve()
HERMES_ROOT = HERMES_SRC
SKILLCLAW_DIR = REPO_ROOT / ".deps" / "SkillClaw"
VENV_PY = HERMES_SRC / ".venv" / "bin" / "python"
EXAMPLE_DIR = Path(__file__).resolve().parent
RUN_ID = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
OUTPUT_DIR = EXAMPLE_DIR / "output" / "oncall-runs" / RUN_ID
SHARED_STORE = OUTPUT_DIR / "l23_shared_store"
MANIFEST_PATH = OUTPUT_DIR / "versions.json"
EVAL_REPORT_PATH = OUTPUT_DIR / "eval_report.json"
DECISION_LOG_PATH = OUTPUT_DIR / "decision_log.json"
SNAPSHOT_DIR = OUTPUT_DIR / "snapshots"
for root in (HERMES_ROOT, SKILLCLAW_DIR, EXAMPLE_DIR):
    sys.path.insert(0, str(root))

from hermes_state import SessionDB  # noqa: E402
from skillclaw.skill_hub import SkillHub  # noqa: E402
from eval.cases import EVAL_CASES, SKILL_V0  # noqa: E402
from eval.runner import run_eval, summarize, compare_versions  # noqa: E402
from eval.judge import self_check  # noqa: E402
from versioning import manifest as vm  # noqa: E402
from versioning import source
from versioning import policy  # noqa: E402
from versioning import snapshot as vsnap  # noqa: E402

from shift import (run_shift, build_review_input, TOOL_SCHEMAS)

SKILL_NAME = "oncall-service-investigation"
GLM_BASE = "https://open.bigmodel.cn/api/paas/v4"
GLM_MODEL = "glm-5.2"
RUN_SOURCE_MANIFEST = None
RUN_STARTED = None
API_KEY = ""  # main 中仅从 GLM_API_KEY 或 BIGMODEL_API_KEY 读取，不写入文件。
PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def sha256(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def redact(text):
    return text.replace(API_KEY, "[密钥已隐去]") if API_KEY else text


def log(text):
    text = redact(str(text))
    print(text, flush=True)
    with (OUTPUT_DIR / "run_results.txt").open("a", encoding="utf-8") as stream:
        stream.write(text + "\n")


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(json.dumps(value, ensure_ascii=False, indent=2)), encoding="utf-8")
    log(f"落盘：{path}")


def evidence(step, value):
    save_json(OUTPUT_DIR / f"step_{step:02d}.json", value)


def section(step, title):
    log(f"\n{'=' * 64}\n步骤 {step}：{title}\n{'=' * 64}")


def chat_completion(payload):
    """禁用环境和系统代理，仅返回模型 message，不保存请求头。"""
    for key in PROXY_KEYS:
        os.environ.pop(key, None)
    key = API_KEY or os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY")
    request = urllib.request.Request(
        f"{GLM_BASE}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=180) as response:
        choice = json.loads(response.read())["choices"][0]
        return dict(choice["message"], _finish_reason=choice.get("finish_reason"))


def llm_call(messages, temperature=0.1, max_tokens=4000):
    message = chat_completion({"model": GLM_MODEL, "messages": messages,
                               "temperature": temperature, "max_tokens": max_tokens})
    return message.get("content") or ""


SKILL_TOOL = {'type': 'function', 'function': {
    'name': 'read_skill', 'description': '实际读取当前隔离实例加载的值守 Skill。',
    'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}


REPORT_TOOL = {'type':'function','function':{
    'name':'submit_report','description':'提交本轮判断与下一状态建议；不改变服务或自动改变状态。完成实际查询后调用。',
    'parameters':{'type':'object','properties':{
        'assessment':{'type':'string','enum':['healthy','suspected','unresolved','recovering','recovered']},
        'report':{'type':'string','description':'中文说明对象、窗口、事实、判断和未决事项'},
        'service_recovered':{'type':'boolean','description':'本轮服务是否满足恢复条件；不代表根因已明'},
        'open_questions':{'type':'array','items':{'type':'object','properties':{'id':{'type':'string'},'question':{'type':'string'},'evidence_refs':{'type':'array','items':{'type':'string'}},'next_check':{'type':'string'}},'required':['id','question','evidence_refs','next_check'],'additionalProperties':False}},
        'next_state':{'type':'string','enum':['PATROLLING','INVESTIGATING','VERIFYING']}},
        'required':['assessment','report','next_state','service_recovered','open_questions'],'additionalProperties':False}}}


def parse_assessment(raw):
    data = json.loads(raw)
    if (not isinstance(data,dict) or set(data)!={'assessment','report','next_state','service_recovered','open_questions'}
        or data['assessment'] not in REPORT_TOOL['function']['parameters']['properties']['assessment']['enum']
        or data['next_state'] not in REPORT_TOOL['function']['parameters']['properties']['next_state']['enum']
        or not isinstance(data['report'],str) or not data['report'].strip()):
        raise ValueError('值守报告字段不合法')
    if type(data['service_recovered']) is not bool or not isinstance(data['open_questions'],list):
        raise ValueError('恢复与未决问题字段不合法')
    for q in data['open_questions']:
        if not isinstance(q,dict) or set(q)!={'id','question','evidence_refs','next_check'} or any(not isinstance(q[k],str) or not q[k].strip() for k in ['id','question','next_check']) or not isinstance(q['evidence_refs'],list) or not all(isinstance(x,str) for x in q['evidence_refs']):
            raise ValueError('未决问题须有编号、问题、来源引用与下一项核查')
    return data


class GlmToolLoopAgent:
    """前台和评测共用的真实工具循环；只记录模型实际请求，不补写调用。"""
    def __init__(self, skill_view, task_prompt, alias, max_turns=6, *,
                 tool_dispatch=None, stage_context=None, skill_path=None,
                 skill_mode='file_read', max_tool_calls=16):
        self.skill_view, self.task_prompt, self.alias = skill_view, task_prompt, alias
        self.max_turns, self.max_tool_calls = max_turns, max_tool_calls
        self.dispatch, self.context = tool_dispatch, stage_context or {}
        self.skill_path, self.skill_mode = skill_path, skill_mode

    def run(self, messages=None):
        import copy
        turn = dict(turn_num=1, prompt_text=self.task_prompt, response_text='',
                    read_skills=[], modified_skills=[], injected_skills=(
                        [SKILL_NAME] if self.skill_mode == 'prompt' else []),
                    tool_calls=[], tool_results=[], tool_errors=[], prm_score=None,
                    source='real_glm_toolloop', skill_sha256=sha256(self.skill_view),
                    skill_mode=self.skill_mode, stop_reason='max_turns', rounds=[],
                    stage_context=self.context)
        if messages is None:
            instruction = (
                '你是只读 On-call 值守助手。先 read_skill 读取当前技能，再根据阶段上下文自主选择工具。'
                '不能将没执行的查询说成已经执行。工具只提供当前逻辑时刻的受控观测。'
                '需要跨轮继续调查时保留线索。最终请调用 submit_report 提交判断，字段为：'
                '{"assessment":"healthy|suspected|unresolved|recovering|recovered",'
                '"report":"中文判断、服务对象、时间窗口、依据和未决事项",'
                '"next_state":"PATROLLING|INVESTIGATING|VERIFYING",'
                '"service_recovered":false,"open_questions":[]}。'
                '未决问题每项必须含 id、question、evidence_refs 字符串列表和 next_check。'
                '调查完成可以建议 VERIFYING，是否结束事故由观测与状态机共同核对。')
            if self.skill_mode == 'prompt':
                instruction += '\n当前技能直接加入提示词：\n' + self.skill_view
            messages = [{'role': 'system', 'content': instruction}]
        else:
            messages = copy.deepcopy(messages)
        turn['message_start'] = len(messages) if len(messages) > 1 else 0
        messages.append({'role': 'user', 'content': self.task_prompt})
        path = OUTPUT_DIR / 'traces' / (self.alias.replace('/', '-') + '.json')
        for number in range(1, self.max_turns+1):
            log(f'{self.alias}：模型请求 {number}/{self.max_turns}')
            try:
                message = chat_completion(dict(model=GLM_MODEL, messages=messages,
                    tools=[SKILL_TOOL, *TOOL_SCHEMAS, REPORT_TOOL], tool_choice='auto', temperature=.2,
                    max_tokens=5000))
            except Exception as exc:
                turn['stop_reason'] = 'model_error'
                turn['model_error'] = f'{type(exc).__name__}: {exc}'
                turn['messages'] = messages
                save_json(path, turn)
                raise
            finish_reason = message.pop('_finish_reason',None)
            calls = message.get('tool_calls') or []
            messages.append(dict(message, role='assistant'))
            record = dict(round_num=number, response_text=message.get('content') or '',
                          tool_calls=calls, tool_results=[],provider_finish_reason=finish_reason)
            turn['rounds'].append(record)
            if not calls:
                turn['response_text'] = message.get('content') or ''
                turn['stop_reason'] = 'final_answer' if turn['response_text'] else 'empty_response'
                try:
                    turn['assessment'] = parse_assessment(turn['response_text'])
                except (ValueError, TypeError) as exc:
                    turn.setdefault('assessment_errors',[]).append(str(exc))
                    turn['stop_reason'] = 'invalid_report'
                    messages.append({'role':'user','content':'报告格式不合法，请用 submit_report 提交；不需要重复查询。'})
                    continue
                break
            for call in calls:
                turn['tool_calls'].append(call)
                fn = call.get('function') or {}
                name, raw = fn.get('name'), fn.get('arguments','')
                try:
                    if len(turn['tool_calls']) > self.max_tool_calls:
                        raise ValueError('本轮调查工具预算已耗尽')
                    arguments = json.loads(raw)
                    if not isinstance(arguments,dict): raise ValueError('参数须是对象')
                    if name == 'submit_report':
                        turn['assessment'] = parse_assessment(raw)
                        turn['response_text'] = raw
                        observation = {'received':True,'note':'仅接收判断，状态转换由当前观测检查'}
                    elif name == 'read_skill':
                        if arguments: raise ValueError('read_skill 不接受参数')
                        content = Path(self.skill_path).read_text() if self.skill_path else self.skill_view
                        if content != self.skill_view: raise ValueError('加载版在运行中变化')
                        observation = {'skill_name':SKILL_NAME,'content':content,'sha256':sha256(content)}
                        turn['read_skills'].append(dict(skill_name=SKILL_NAME,
                            source='read_skill_tool', path=str(self.skill_path), sha256=sha256(content)))
                    else:
                        observation = self.dispatch(name, arguments)
                    stdout, stderr, code = json.dumps(observation,ensure_ascii=False), '', 0
                except (ValueError,TypeError,OSError) as exc:
                    stdout, stderr, code = '', str(exc), 2
                result = dict(tool_call_id=call['id'], command=f'{name}({raw})',has_error=code!=0,
                    content=json.dumps(dict(stdout=stdout,stderr=stderr,exit_code=code),ensure_ascii=False))
                turn['tool_results'].append(result)
                record['tool_results'].append(result)
                if code: turn['tool_errors'].append(result)
                messages.append(dict(role='tool',tool_call_id=call['id'],content=result['content']))
            turn['messages'] = messages
            save_json(path,turn)
            if turn.get('assessment'):
                turn['stop_reason'] = 'final_answer'
                break
            if len(turn['tool_calls']) >= self.max_tool_calls:
                turn['stop_reason'] = 'tool_budget'
                break
        turn['messages'] = messages
        save_json(path,turn)
        log(f"{self.alias} 停止原因：{turn['stop_reason']}；{turn['response_text']}")
        return turn


def turn_runner(skill_path, prefix):
    def run(prompt, ref, dispatch, context, messages):
        return GlmToolLoopAgent(Path(skill_path).read_text(), prompt, prefix+'-'+ref,
            tool_dispatch=dispatch, stage_context=context, skill_path=skill_path,
            max_tool_calls=min(16, context['investigation_budget_remaining'])).run(messages)
    return run


def persist_session(db, session, cwd):
    sid = session['session_id']
    db.create_session(sid, 'oncall', model=GLM_MODEL, cwd=str(cwd))
    db.set_session_title(sid, 'On-call '+sid)
    for turn in session['turns']:
        for msg in turn['messages'][turn['message_start']:]:
            db.append_message(sid, msg['role'], content=msg.get('content'),
                tool_calls=msg.get('tool_calls'), tool_call_id=msg.get('tool_call_id'),
                reasoning_content=msg.get('reasoning_content'))
    save_json(OUTPUT_DIR / 'collected_sessions' / f'{sid}.json', session)


def upload_sessions(hub, sessions, review):
    keys = []
    for session in sessions:
        key = f"{hub._prefix()}sessions/{session['session_id']}.json"
        hub._bucket.put_object(key,json.dumps(session,ensure_ascii=False).encode())
        keys.append(key)
    review_key = f"{hub._prefix()}shift_reviews/{review['shift_id']}.json"
    hub._bucket.put_object(review_key,json.dumps(review,ensure_ascii=False).encode())
    queued = [json.loads(p.read_text()) for p in (SHARED_STORE/'default'/'sessions').glob('*.json')]
    expected = {s['session_id'] for s in sessions}
    actual = {s['session_id'] for s in queued}
    assert expected <= actual, '共享会话编号缺漏'
    for session in sessions:
        assert next(s for s in queued if s['session_id']==session['session_id']) == session
    return dict(keys=keys, review_key=review_key, expected_ids=sorted(expected),
                uploaded_ids=sorted(actual), complete=True)


def run_oncall_case(skill_text, version, case):
    path = OUTPUT_DIR / 'eval_skills' / version / 'SKILL.md'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(skill_text)
    # 不把 case 的评分标准、预期结论或未来快照传入运行器。
    sid = f'{RUN_ID}-{version}-{case["id"]}'
    session, state = run_shift(scenario_id=case['id'],ticks=[5,10],
        run_turn=turn_runner(path,'eval'),run_id=RUN_ID,shift_id=sid,session_id=sid,
        events_path=OUTPUT_DIR/'eval_transitions'/f'{sid}.jsonl',end_shift=True)
    execution = dict(session=session,state=state)
    save_json(OUTPUT_DIR/'eval_executions'/f'{sid}.json',execution)
    return execution


def manifest_version(store):
    path = store / "default" / "manifest.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    versions = [int(row.get("version", 0)) for row in records if row.get("name") == SKILL_NAME]
    return max(versions, default=0)


def run_evolve(store, publish_mode, tag):
    env = dict(os.environ)
    for key in PROXY_KEYS:
        env.pop(key, None)
    env.update({
        "OPENAI_API_KEY": API_KEY, "OPENAI_BASE_URL": GLM_BASE,
        "EVOLVE_MODEL": GLM_MODEL, "EVOLVE_USE_SESSION_JUDGE": "1",
        "PYTHONPATH": str(SKILLCLAW_DIR),
        "EVOLVE_HISTORY_LOG": str(OUTPUT_DIR / f"evolve_history_{tag}.jsonl"),
        "EVOLVE_PROCESSED_LOG": str(OUTPUT_DIR / f"evolve_processed_{tag}.json"),
    })
    command = [str(VENV_PY), "-m", "evolve_server", "--engine", "workflow", "--once",
               "--storage-backend", "local", "--local-root", str(store),
               "--model", GLM_MODEL, "--publish-mode", publish_mode]
    log(f"执行：{' '.join(command)}")
    proc = subprocess.run(command, cwd=SKILLCLAW_DIR, env=env, capture_output=True, text=True)
    for label, value in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        path = OUTPUT_DIR / f"evolve_{tag}.{label}.txt"
        path.write_text(redact(value), encoding="utf-8")
        log(f"后台输出：{path}")
    if proc.returncode:
        raise RuntimeError(f"evolve_server 退出码 {proc.returncode}；详见后台输出，不视为 skip")
    start = proc.stdout.find("{")
    if start < 0:
        raise RuntimeError("evolve_server 未返回 JSON summary，不视为 skip")
    summary, _ = json.JSONDecoder().raw_decode(proc.stdout[start:])
    if not isinstance(summary, dict):
        raise RuntimeError("evolve_server summary 不是对象")
    return summary


def main():
    global API_KEY, RUN_SOURCE_MANIFEST
    started = time.monotonic()
    global RUN_STARTED
    RUN_STARTED = started
    API_KEY = os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY") or ""
    if not API_KEY.strip():
        raise ValueError("请设置 GLM_API_KEY 或 BIGMODEL_API_KEY，密钥不能为空")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
    (EXAMPLE_DIR / 'output' / 'latest_oncall_run.txt').write_text(str(OUTPUT_DIR)+'\n')
    source_manifest = source.fingerprint(REPO_ROOT, __file__, (HERMES_SRC, SKILLCLAW_DIR))
    RUN_SOURCE_MANIFEST = source_manifest
    save_json(OUTPUT_DIR / "source_manifest.json", source_manifest)
    SNAPSHOT_DIR.mkdir()
    section(0, "准备独立实例与共享存储")
    a_home = OUTPUT_DIR / "HERMES_HOME_A"
    a_home.mkdir()
    a_skills_dir = a_home / "skills"
    a_skills_dir.mkdir()
    (a_home / "memories").mkdir()
    os.environ["HERMES_HOME"] = str(a_home)
    hub = SkillHub(backend="local", endpoint="", bucket="", access_key_id="", secret_access_key="",
                   local_root=str(SHARED_STORE), group_id="default", user_alias="instance-A")
    evidence(0, {"A_HOME": str(a_home), "SHARED_STORE": str(SHARED_STORE)})

    section(1, "基线写入正常加载目录并发布")
    v0_content = SKILL_V0
    loaded_path = a_skills_dir / SKILL_NAME / "SKILL.md"
    loaded_path.parent.mkdir()
    loaded_path.write_text(v0_content, encoding="utf-8")
    push = hub.push_skills(str(a_skills_dir))
    log(f"基线 push：{push}；v0 SHA-256={sha256(v0_content)}")
    assert manifest_version(SHARED_STORE) == 1, "基线共享版本必须为 1"
    shared_path = SHARED_STORE / "default" / "skills" / SKILL_NAME / "SKILL.md"
    assert shared_path.read_text(encoding="utf-8") == v0_content
    db_path = OUTPUT_DIR / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        evidence(1, {"push": push, "v0_sha256": sha256(v0_content),
                     "loaded_path": str(loaded_path), "db_path": str(db_path), "shared_version": 1})
        section(2, "两段多轮真实 On-call 值守会话")
        shift_id = RUN_ID + '-A-shift'
        shared = dict(scenario_id='training',run_turn=turn_runner(loaded_path,'A'),
                      run_id=RUN_ID,shift_id=shift_id,events_path=OUTPUT_DIR/'transitions.jsonl')
        first, state = run_shift(**shared,ticks=[0,5],session_id=shift_id+'-S1')
        second, state = run_shift(**shared,ticks=[10,15,20,25],session_id=shift_id+'-S2',
                                 state=state,investigation_record=state['records'],end_shift=True)
        sessions = [first,second]
        review = build_review_input(sessions,state)
        for session in sessions:
            session['review_ref'] = f"default/shift_reviews/{shift_id}.json"
            persist_session(db,session,a_home)
        save_json(OUTPUT_DIR/'review_input.json',review)
        evidence(2,dict(session_ids=review['session_ids'],review_ref='review_input.json',
                        total_turns=sum(s['num_turns'] for s in sessions),state=state))
        section(3, "上传实际会话和班次复盘记录，按编号核对完整性")
        uploaded = upload_sessions(hub,sessions,review)
        assert set(uploaded['uploaded_ids']) == set(review['session_ids'])
        evidence(3,uploaded)

        section(4, "真实 workflow validated 后台演化")
        before = manifest_version(SHARED_STORE)
        summary = run_evolve(SHARED_STORE, "validated", "l23")
        log(f"sessions={summary.get('sessions')} skill_groups={summary.get('skill_groups')} "
            f"skills_evolved={summary.get('skills_evolved')} elapsed={summary.get('elapsed_seconds')}")
        for ev in summary.get("evolutions", []):
            log(f"action={ev.get('action')} skill_name={ev.get('skill_name')} "
                f"version={ev.get('version')} uploaded={ev.get('uploaded')} "
                f"rationale={str(ev.get('rationale', ''))[:240]}")
        log(f"session_judge={summary.get('session_judge')}")
        after = manifest_version(SHARED_STORE)
        assert before == after == 1, "validated 不得推进共享版本"
        assert shared_path.read_text(encoding="utf-8") == v0_content, "validated 不得覆盖共享基线"
        assert loaded_path.read_text(encoding="utf-8") == v0_content, "候选不得提前进入 A 加载目录"
        jobs = sorted((SHARED_STORE / "default" / "validation_jobs").glob("*.json"),
                      key=lambda p: (p.stat().st_mtime_ns, p.name))
        log(f"validation_jobs：{[str(p) for p in jobs]}")
        candidate_content, candidate_path = None, None
        if jobs:
            job = json.loads(jobs[-1].read_text(encoding="utf-8"))
            assert job["candidate_skill_name"] == SKILL_NAME, "候选技能名称不符"
            job_id = job["job_id"]
            assert isinstance(job_id, str) and Path(job_id).name == job_id and job_id not in (".", "..")
            candidate_path = SHARED_STORE / "default" / "candidate_skills" / job_id / "SKILL.md"
            candidate_content = candidate_path.read_text(encoding="utf-8")
            assert candidate_content.strip(), "候选文件为空"
            log(f"候选：{candidate_path}\n候选全文预览（前 600 字）：\n{candidate_content[:600]}")
            diff = "\n".join(difflib.unified_diff(v0_content.splitlines(), candidate_content.splitlines(),
                                                fromfile="v0", tofile="candidate-v1", lineterm=""))
            (OUTPUT_DIR / "candidate.diff").write_text(diff, encoding="utf-8")
            log(f"新增/变化行：\n{diff}")
        else:
            orphan_candidates = list((SHARED_STORE / "default" / "candidate_skills").glob("*/SKILL.md"))
            assert not orphan_candidates, "存在无 validation job 的候选，停止核查"
            log("本轮演化未产出候选，闭环以 ROLLBACK 结束")
        evidence(4, {"summary": summary, "before": before, "after": after,
                     "candidate_path": str(candidate_path) if candidate_path else None})

        section(5, "隔离评测")
        v0_eval = v1_eval = cmp = sc = None
        if candidate_content is None:
            log("无候选，跳过版本对比和评分程序自检；不填造通过率。")
        else:
            v0_eval = run_eval(v0_content, "v0", llm_call, run_case=run_oncall_case)
            save_json(OUTPUT_DIR / "eval_v0.json", v0_eval)
            v1_eval = run_eval(candidate_content, "v1", llm_call, run_case=run_oncall_case)
            save_json(OUTPUT_DIR / "eval_v1.json", v1_eval)
            sc = self_check(llm_call)
            cmp = compare_versions(v0_eval, v1_eval)
            for subset in ("new_incident", "holdout", "regression"):
                log(f"{subset}：{cmp['v0_summary'][subset]['pass_rate']:.0%} -> "
                    f"{cmp['v1_summary'][subset]['pass_rate']:.0%}")
            log(f"overall：{summarize(v0_eval)['overall']} -> {summarize(v1_eval)['overall']}")
            log(f"self_check 实际结果：{sc}")
        report = {"source_sha256": source_manifest["sha256"], "v0": v0_eval, "v1": v1_eval, "comparison": cmp, "self_check": sc,
                  "case_count": len(EVAL_CASES), "skipped": candidate_content is None}
        save_json(EVAL_REPORT_PATH, report)
        evidence(5, {"report": str(EVAL_REPORT_PATH), "skipped": report["skipped"]})

        section(6, "先验证评分器和检查完整性，再按三标准决定采用或回退")
        decision = policy.decide(cmp, self_check=sc, v0_eval=v0_eval, v1_eval=v1_eval,
            expected_skill_hashes={"v0": sha256(v0_content), "v1": sha256(candidate_content)}) if cmp is not None else {
            "decision": "ROLLBACK", "checks": [],
            "reason_summary": "本轮演化未产出候选，闭环以 ROLLBACK 结束；三标准未执行。",
        }
        if not review["complete"]:
            decision = {"decision":"REJECT", "status":"WAIT_FOR_MANUAL_REVIEW", "checks":[],
                        "reason_summary":"前台值守经历不完整，禁止采用"}
        if not source.unchanged(source_manifest, REPO_ROOT):
            decision = {"decision": "REJECT", "status": "WAIT_FOR_MANUAL_REVIEW", "checks": [],
                        "reason_summary": "运行中源码发生变化，禁止自动采用"}
        decision["source_sha256"] = source_manifest["sha256"]
        log(f"decision={decision['decision']}；{decision['reason_summary']}")
        for check in decision["checks"]:
            log(f"pass={check['pass']}：{check['detail']}")
        save_json(DECISION_LOG_PATH, decision)
        evidence(6, decision)

        section(7, "切换正常加载目录、发布采用版并写恢复点")
        adopted_version = "v1" if decision["decision"] == "ADOPT" else "v0"
        adopted_content = candidate_content if adopted_version == "v1" else v0_content
        if adopted_version == "v1":
            loaded_path.write_text(adopted_content, encoding="utf-8")
            push = hub.push_skills(str(a_skills_dir))
            log(f"采用版 push：{push}")
            assert manifest_version(SHARED_STORE) == 2, "采用版应推进共享版本到 2"
            log("已把候选从 candidate_skills 提升到正常加载目录 skills/ 并发布")
        elif candidate_content is not None:
            log("候选未通过评测，保留在 candidate_skills/，不进入加载目录")
        else:
            log("无候选，正常加载目录和共享存储保持 v0。")
        assert loaded_path.read_text(encoding="utf-8") == adopted_content
        assert shared_path.read_text(encoding="utf-8") == adopted_content
        assert manifest_version(SHARED_STORE) == (2 if adopted_version == "v1" else 1)
        snap = vsnap.take_snapshot(db, adopted_version, adopted_content, SNAPSHOT_DIR)
        entry = vm.record_version(MANIFEST_PATH, SKILL_NAME, adopted_content,
                                  decision=decision["decision"], eval_report=EVAL_REPORT_PATH.name,
                                  notes=decision["reason_summary"],
                                  candidate_content=candidate_content, adopted_label=adopted_version)
        report.update({"adopted": adopted_version, "adopted_sha256": sha256(adopted_content),
                       "manifest_entry_id": entry["version"]})
        save_json(EVAL_REPORT_PATH, report)
        log(f"版本记录：{MANIFEST_PATH}；记录序号 {entry['version']}，采用技能标签 {adopted_version}")
        log(f"恢复点：{snap}")
        db.create_session("oncall-extra", "lab", model=GLM_MODEL)
        db.append_message("oncall-extra", "user", content="快照之后添加的消息，恢复后应消失。")
    finally:
        db.close()
    restored = vsnap.restore_snapshot(db_path, adopted_version, SNAPSHOT_DIR)
    restored_db = SessionDB(db_path=db_path)
    try:
        assert restored_db.get_session("oncall-extra") is None
        assert all(restored_db.get_session(s["session_id"]) is not None for s in sessions)
        restored_skill = vsnap.restore_skill(adopted_version, SNAPSHOT_DIR)
        assert sha256(restored_skill) == sha256(adopted_content)
        loaded_path.write_text(restored_skill)
        restored["skill_sha256"] = sha256(loaded_path.read_text())
        assert restored["sessions"] == snap["sessions"] and restored["messages"] == snap["messages"]
    finally:
        restored_db.close()
    log(f"回退后：{restored['sessions']} 会话、{restored['messages']} 消息；oncall-extra 已消失")
    evidence(7, {"adopted": adopted_version, "loaded_path": str(loaded_path),
                 "manifest_entry": entry, "snapshot": snap, "restored": restored})

    section(8, "第三实例真实 pull 并加载后值班")
    c_home = OUTPUT_DIR / "HERMES_HOME_C"
    c_home.mkdir()
    c_skills_dir = c_home / "skills"
    c_skills_dir.mkdir()
    result = hub.pull_skills(str(c_skills_dir), mirror=False, include_names=[SKILL_NAME])
    log(f"第三实例 pull：{result}")
    assert result["downloaded"] >= 1, "第三实例未下载技能"
    c_path = c_skills_dir / SKILL_NAME / "SKILL.md"
    pulled_content = c_path.read_text(encoding="utf-8")
    assert sha256(pulled_content) == sha256(adopted_content)
    log(f"第三实例 {c_path} SHA-256={sha256(pulled_content)}，与采用版一致")
    (OUTPUT_DIR/'C_loaded_SKILL.md').write_text(pulled_content)
    c_session, c_state = run_shift(scenario_id='c_short',ticks=[30,35],
        run_turn=turn_runner(c_path,'C'),run_id=RUN_ID,shift_id=RUN_ID+'-C-shift',
        session_id=RUN_ID+'-C-S1',events_path=OUTPUT_DIR/'C_transitions.jsonl',
        end_shift=True,user_alias='instance-C')
    c_review = build_review_input([c_session],c_state)
    c_session['review_ref'] = f"default/shift_reviews/{c_state['shift_id']}.json"
    c_db = SessionDB(db_path=c_home/'state.db')
    try:
        persist_session(c_db,c_session,c_home)
    finally:
        c_db.close()
    c_feedback = upload_sessions(hub,[c_session],c_review)
    c_execution_ok = c_review['complete'] and all(
        t['assessment'].get('assessment') == 'healthy' for t in c_session['turns'])
    evidence(8,dict(pull=result,skill_path=str(c_path),sha256=sha256(pulled_content),
                   hash_matches_adopted=True,execution_ok=c_execution_ok,
                   session=c_session,feedback=c_feedback))

    section(9, "总结与诚实边界")
    stance = (
        "本轮受控巡检参考实现记录候选生成、评测决定与基线继续服务的路径；"
        "候选未被采用时，尚未证明下一轮任务因新版而改善。"
        "A本地快照恢复与C新会话读取采用版分别验证，不代表跨实例续接原调查。"
    )
    log(stance)
    log(f"本次实际分支：候选存在={candidate_content is not None}，decision={decision['decision']}，采用={adopted_version}。")
    log("边界：前台跑 glm-5.2 + 只读 mock 工具循环作为工具循环参考实现，工具返回受控测试数据，未起完整 Hermes 实例。")
    log("边界：跨实例使用本地文件后端共享存储；未验收跨主机共享或生产部署。")
    log("边界：后台演化同步等待；未验收生产异步调度。")
    paths = {f"步骤 {step}": str(OUTPUT_DIR / f"step_{step:02d}.json") for step in range(10)}
    paths.update({"运行记录": str(OUTPUT_DIR / "run_results.txt"), "数据库": str(db_path),
                  "评测报告": str(EVAL_REPORT_PATH), "版本决策": str(DECISION_LOG_PATH),
                  "版本记录": str(MANIFEST_PATH), "恢复点": str(SNAPSHOT_DIR),
                  "共享存储": str(SHARED_STORE), "A 加载文件": str(loaded_path), "C 下载文件": str(c_path)})
    evidence(9, {"stance": stance, "decision": decision, "adopted": adopted_version, "paths": paths})
    save_json(OUTPUT_DIR/'run_summary.json',dict(
        run_id=RUN_ID,status='COMPLETE',elapsed_seconds=round(time.monotonic()-started,2),
        source_sha256=source_manifest['sha256'],time_mode='时间窗口由逻辑时钟模拟推进，不代表真实等待时长',
        shift_complete=review['complete'],shift_session_count=len(sessions),
        shift_turn_count=sum(s['num_turns'] for s in sessions),
        unresolved=review['unresolved'],transitions=state['transitions'],
        decision=decision,adopted=adopted_version,
        candidate_sha256=sha256(candidate_content) if candidate_content else None,
        adopted_sha256=sha256(adopted_content),
        self_check=sc,comparison=cmp,c_execution_ok=c_execution_ok,
        c_feedback_complete=c_feedback['complete'],c_sha256=sha256(pulled_content),
        snapshot_restored=restored,paths=paths))
    for label, path in paths.items():
        log(f"{label}：{path}")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        if RUN_SOURCE_MANIFEST is not None:
            identity = RUN_SOURCE_MANIFEST
            decision = {"decision": "REJECT", "status": "WAIT_FOR_MANUAL_REVIEW",
                        "checks": [{"check": "evaluation_complete", "pass": False,
                                    "detail": "运行异常，评测或后续验证未完成"}],
                        "reason_summary": f"运行未完成：{type(exc).__name__}: {exc}",
                        "source_sha256": identity["sha256"]}
            # 已作出的决策单独保留，不能把后续验证异常伪装成评测成功。
            if DECISION_LOG_PATH.exists():
                decision = json.loads(DECISION_LOG_PATH.read_text())
            else:
                save_json(DECISION_LOG_PATH, decision)
            save_json(OUTPUT_DIR / "run_summary.json", {
                "status": "INCOMPLETE", "stop_reason": "error", "decision": decision,
                "elapsed_seconds": round(time.monotonic()-RUN_STARTED,2),
                "error": f"{type(exc).__name__}: {exc}",
                "source_sha256": identity["sha256"]})
        raise
