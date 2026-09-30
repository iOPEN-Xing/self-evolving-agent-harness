#!/usr/bin/env python3
"""真实 Hermes 薄适配：预载 Skill 与当题只读快照，模型只作一次业务回答。"""
import contextlib
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
from readonly_tools import read_table

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
SOURCE = Path(os.environ.get('LECTURE_HERMES_SOURCE', str(REPO / '.deps/hermes-agent'))).resolve()

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def invoke(payload, probe=False):
    key = os.environ.get('GLM_API_KEY', '')
    base = os.environ.get('GLM_BASE_URL', '')
    if not probe and (not key or not base):
        return dict(exit_code=78, final_message='须显式设置 GLM_API_KEY 和 GLM_BASE_URL；未发起模型请求。', transcript=[])
    runtime = Path(os.environ['COURSE_RUNTIME_DIR'])
    runtime.mkdir(parents=True, exist_ok=True)
    home = runtime / 'hermes-home'
    os.environ['HERMES_HOME'] = str(home)
    skilldir = home / 'skills/payment-report'
    skilldir.mkdir(parents=True)
    shutil.copy2(ROOT / 'skill/payment-report/SKILL.md', skilldir / 'SKILL.md')
    (home / 'config.yaml').write_text('memory:\n  memory_enabled: false\n  user_profile_enabled: false\nskills:\n  creation_nudge: false\ntools:\n  tool_search:\n    enabled: false\n')
    sys.path.insert(0, str(SOURCE))
    sys.dont_write_bytecode = True
    import hermes_cli.env_loader
    hermes_cli.env_loader.load_hermes_dotenv = lambda **kwargs: []
    from tools.skills_tool import skill_view
    from agent.skill_commands import build_preloaded_skills_prompt
    raw = (skilldir / 'SKILL.md').read_bytes()
    loaded_result = json.loads(skill_view('payment-report', preprocess=False))
    loaded = loaded_result.get('raw_content', loaded_result.get('content', ''))
    preloaded, names, missing = build_preloaded_skills_prompt(['payment-report'])
    assert not missing and names == ['payment-report']
    assert digest(loaded.encode()) == digest(raw) and loaded.strip() in preloaded
    (runtime / 'preloaded-prompt.txt').write_text(preloaded)
    workspace = Path(payload['workspace']).resolve()
    tables = {name: read_table(workspace, name) for name in ['request', 'orders', 'ledger', 'channel']}
    evidence = dict(method='adapter_preloaded_skill_and_snapshots', request_id=tables['request']['request_id'],
                    skill_loaded=True, skill_name='payment-report', skill_sha256=digest(raw), loaded_sha256=digest(loaded.encode()),
                    preloaded_prompt_sha256=digest(preloaded.encode()), adapter_table_reads=sorted(tables),
                    input_sha256={name:digest((workspace/'input'/f'{name}.json').read_bytes()) for name in tables},
                    model_tool_calls=[], tool_names=[], verified=True, api_requests_attempted=0,
                    api_responses_received=0, successful_http_responses=0, upstream_state={})
    def flush():
        (runtime / 'execution-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    flush()
    if probe:
        unexpected = []
        for bad in ['../grader-only/payment-001', '../../manifest', 'oracle', 'request.json']:
            try:
                read_table(workspace, bad)
                unexpected.append(bad)
            except PermissionError:
                pass
        return dict(skill_loaded=True, skill_name='payment-report', preload_method='build_preloaded_skills_prompt',
                    blocked_paths=4-len(unexpected), unexpected_allowed=unexpected,
                    input_table_read=tables['request']['request_id'], live_model_called=False)
    from run_agent import AIAgent
    original_http_builder = AIAgent._build_keepalive_http_client
    transport_events = []
    def record_http(event):
        event['time'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        transport_events.append(event)
        (runtime / 'transport-events.json').write_text(json.dumps(transport_events, ensure_ascii=False, indent=2)+'\n')
        flush()
    def observed_http_builder(base_url='', **kwargs):
        client = original_http_builder(base_url, **kwargs)
        if client is None:
            raise RuntimeError('HTTP 观测客户端不可用')
        def request_hook(request):
            body = json.loads(request.content)
            shape = dict(event='request', host=request.url.host, model=body.get('model'),
                         response_format=body.get('response_format'), thinking=body.get('thinking'),
                         temperature=body.get('temperature'), stream=body.get('stream'),
                         tools=[x.get('function', x).get('name') for x in body.get('tools', [])])
            evidence['api_requests_attempted'] += 1
            record_http(shape)
            assert shape['tools'] == [], '预载流程意外暴露了工具'
            assert shape['response_format'] == {'type':'json_object'}
            assert shape['thinking'] == {'type':'disabled'} and shape['temperature'] == 0
        def response_hook(response):
            evidence['api_responses_received'] += 1
            evidence['successful_http_responses'] += int(response.status_code == 200)
            record_http(dict(event='response', status=response.status_code))
        client.event_hooks['request'].append(request_hook)
        client.event_hooks['response'].append(response_hook)
        return client
    AIAgent._build_keepalive_http_client = staticmethod(observed_http_builder)
    run_id = 'hermes-' + uuid.uuid4().hex
    machine = '这是由程序解析的机器接口。完整 Skill 和当题四张只读快照均已预载，无须再调用工具。只输出约定格式的纯 JSON，所有解释写入 explanation 字段，不写分析前后缀或代码围栏。无用户后续答复，不得凭空补充资料。'
    agent = AIAgent(model='glm-5.2', base_url=base, api_key=key, provider='custom', api_mode='chat_completions',
                    enabled_toolsets=[], max_iterations=1, max_tokens=4096,
                    request_overrides={'response_format':{'type':'json_object'}, 'temperature':0,
                                       'extra_body':{'thinking':{'type':'disabled'}}},
                    ephemeral_system_prompt=preloaded+'\n\n'+machine, quiet_mode=True, skip_context_files=True,
                    skip_memory=True, load_soul_identity=False, save_trajectories=False, session_id=run_id)
    assert agent.tools == [], 'enabled_toolsets=[] 未得到空工具集'
    agent.compression_enabled = False
    prompt = '\n'.join(m['content'] for m in payload['messages'] if m['role'] == 'user')
    prompt += '\n\n以下是运行器原样读取的当题 request、orders、ledger、channel 快照；所有时间均为 UTC：\n' + json.dumps(tables, ensure_ascii=False)
    started = time.monotonic()
    result = agent.run_conversation(prompt, task_id=run_id)
    safe_result = {k:result.get(k) for k in ['final_response', 'messages', 'api_calls', 'completed', 'turn_exit_reason',
                    'failed', 'partial', 'interrupted', 'response_transformed', 'model', 'provider',
                    'prompt_tokens', 'completion_tokens', 'total_tokens']}
    (runtime / 'hermes-result.json').write_text(json.dumps(safe_result, ensure_ascii=False, indent=2)+'\n')
    final = result.get('final_response') or ''
    failed = bool(result.get('failed') or result.get('interrupted') or result.get('error') or result.get('partial') or result.get('completed') is not True)
    evidence['upstream_state'] = {k:result.get(k) for k in ['failed', 'interrupted', 'partial', 'completed', 'failure_reason', 'turn_exit_reason']}
    evidence['model_tool_calls'] = [t for m in result.get('messages', []) for t in m.get('tool_calls', [])]
    assert not evidence['model_tool_calls'], '预载流程出现意外工具调用'
    evidence['api_calls'] = result.get('api_calls')
    flush()
    return dict(engine='hermes', model='glm-5.2', applied_model=result.get('model'), session_id=run_id,
                exit_code=0 if final and not failed else 1, duration_ms=int((time.monotonic()-started)*1000),
                turns=1, final_message=final, transcript=[dict(role='user', content=prompt), dict(role='assistant', content=final)],
                warnings=[] if not failed else ['Hermes 未正常完成；保留原始运行记录。'])

if __name__ == '__main__':
    payload = json.loads(Path(sys.argv[1]).read_text())
    dest = Path(sys.argv[2])
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = invoke(payload, '--probe' in sys.argv)
    except Exception as exc:
        result = dict(exit_code=70, final_message='Hermes 运行失败：'+type(exc).__name__, transcript=[])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
