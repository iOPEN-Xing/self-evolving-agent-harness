#!/usr/bin/env python3
"""总装真实验收：all，或 smoke/learn/candidate/eval/shared/snapshot。

先 source ~/.hermes/.env，再用 .deps/hermes-agent/.venv/bin/python -B 运行。
--run-id 可继续同一次运行；已成功阶段经来源校验后复用，不反复搜索赢家。
所有模块只 import 复用。父进程把 config.OUTPUT_DIR 的内存值指向本轮
output/assembly-runs/<id>，避免覆盖四模块旧证据；源码配置不变。
Hermes 子进程仍使用原 output/homes/<instance>，并先设置环境再导入。
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
from assembly import config
from assembly.contracts import (BackgroundReviewResult, CandidateBundle, SharedRevision,
                                sha256_text, tree_hashes, write_json)

OUTPUT = config.OUTPUT_DIR
SCRIPT = Path(__file__).resolve()
PYTHON = config.HERMES_SRC / '.venv/bin/python'
STAGES = ('smoke', 'learn', 'candidate', 'eval', 'shared', 'snapshot')
REQUIRES = {'smoke': (), 'learn': (), 'candidate': ('learn',), 'eval': ('candidate',),
            'shared': ('eval',), 'snapshot': ('eval',)}


def require_environment():
    if not os.environ.get('GLM_API_KEY', '').strip():
        raise RuntimeError('先把 GLM_API_KEY 加载到环境变量；本入口不读取密钥文件。')
    os.environ.pop('BIGMODEL_API_KEY', None)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    config.clear_proxy_for_model()
    if config.MODEL != 'glm-5.2' or config.BASE_URL != 'https://open.bigmodel.cn/api/paas/v4':
        raise ValueError('本次验收固定 glm-5.2 与指定智谱端点。')
    config.ensure_paths()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(value):
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def identity(directory):
    from assembly.eval.runner import skill_tree_hash
    manifest = tree_hashes(directory)
    return {'directory': str(directory), 'skill_file_hash': manifest['SKILL.md'],
            'skill_tree_hash': skill_tree_hash(directory), 'manifest': manifest}


def protected_files():
    paths = list((config.ASSEMBLY_DIR / 'assembly').rglob('*.py'))
    paths += list(config.SCENARIOS_DIR.rglob('*'))
    paths += list(config.ASSEMBLY_DIR.glob('run_*_demo.py'))
    paths += [OUTPUT / name / 'SUMMARY.md' for name in ('runtime', 'skillclaw', 'lifecycle', 'eval')]
    for repo in (config.HERMES_SRC, config.SKILLCLAW_SRC):
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=repo).decode().split('\0')
        paths += [repo / name for name in tracked if name]
    return {str(p.relative_to(config.REPO_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(paths)) if p.is_file()}


def worker(request_file):
    """每次只创建一个原生 Hermes 实例；不在父进程切换 Hermes 全局路径。"""
    request = read(request_file)
    instance, home = request['instance'], Path(request['home'])
    evidence, adopted = Path(request['evidence']), Path(request['adopted'])
    check(not home.exists(), '拒绝复用已有 Hermes home。')
    local_skill = home / 'skills' / config.SKILL_NAME
    shutil.copytree(adopted, local_skill)
    before = tree_hashes(local_skill)
    os.environ['HERMES_HOME'] = str(home)
    from assembly.runtime.foreground import make_agent
    from assembly.runtime.background import measure_nonblocking
    agent = make_agent(instance, home)
    error = None
    try:
        # 精确记录实际送入模型的版本；磁盘存在本身不能证明模型读取过。
        skill_text = (local_skill / 'SKILL.md').read_text(encoding='utf-8')
        prompt = (request['prompt'] + '\n\n本轮已加载技能正文：\n' + skill_text)
        write_json(evidence / 'loaded_context.json', {
            'path': str(local_skill / 'SKILL.md'), 'sha256': sha256_text(skill_text),
            'content': skill_text, 'delivery': '读取实例技能后作为实际 user_message 的一部分传入',
        })
        try:
            measure_nonblocking(agent, instance, prompt, evidence_dir=evidence,
                                timeout_sec=request['background_timeout'])
        except Exception as exc:
            # 原入口在 finally 保存全部观测；后台失败不是前台失败。
            error = {'type': type(exc).__name__,
                     'message': str(exc).replace(os.environ['GLM_API_KEY'], '[密钥已隐藏]')}
        check((evidence / 'foreground.json').exists(), '前台没有产生 TaskRun。')
        task = read(evidence / 'foreground.json')
        observation = read(evidence / 'observation.json')
        raw_bg = read(evidence / 'background.json')
        if error:
            raw_bg['failed'] = True
            raw_bg['error'] = '\n'.join(filter(None, [raw_bg['error'], error['message']]))
            proof = observation['nonblocking_evidence']
            raw_bg['blocked_foreground'] = not all(proof.get(k) for k in (
                'native_daemon', 'required_foregrounds_returned',
                'join_requested_after_all_foregrounds', 'first_review_alive_after_foreground_return'))
        bg = BackgroundReviewResult(**raw_bg)
        write_json(evidence / 'background_result.json', bg)
        messages = copy.deepcopy(agent._session_messages)
        write_json(evidence / 'messages.json', messages)
        persisted = agent._runtime_db.get_messages(task['session_id'])
        session = agent._runtime_db.get_session(task['session_id'])
        write_json(evidence / 'persisted_messages.json', persisted)
        write_json(evidence / 'persisted_session.json', session)
        check(task['stop_reason'] == 'final_answer', '真实前台没有正常结束。')
        calls = [c for c in task['tool_calls'] if c['name'] == 'query_order_payments']
        check(calls and all(not c['has_error'] for c in calls), '缺少成功支付工具回执。')
        check(any(c['arguments'].get('order_id') == request['order_id'] for c in calls),
              '前台没有实际查询指定订单。')
        check(task['skill_hash'] == before['SKILL.md'], 'TaskRun 与实际输入技能哈希不一致。')
        persisted_answer = any(m.get('role') == 'assistant' and m.get('content') == task['answer']
                               for m in persisted)
        persisted_tools = any(m.get('role') == 'tool' for m in persisted)
        check(session and persisted_answer and persisted_tools, 'SQLite 未保存最终答复或工具记录。')
        result = {'task': task, 'background': bg.to_dict(), 'home': str(home),
                  'evidence': str(evidence), 'input_manifest': before,
                  'post_review_manifest': tree_hashes(home / 'skills'), 'review_exception': error,
                  'persistence': {'session_id': task['session_id'], 'message_count': len(persisted),
                                  'answer_matches': persisted_answer, 'tool_receipts_present': persisted_tools,
                                  'database': str(home / 'state.db')}}
        write_json(evidence / 'result.json', result)
    finally:
        agent.close()
        agent._runtime_db.close()


class Assembly:
    def __init__(self, run_id):
        check(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,70}', run_id)), 'run-id 不合法。')
        self.root = OUTPUT / 'assembly-runs' / run_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / 'run.lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.path = self.root / 'trace.json'
        # 只改变本进程的输出根；模块源码、场景和旧验收目录只读。
        config.OUTPUT_DIR = self.root
        from assembly.lifecycle.skills import init_adopted
        self.adopted = self.root / 'adopted' / config.SKILL_NAME
        if self.path.exists():
            self.trace = read(self.path)
            check(self.trace['protected_before'] == protected_files(), '来源文件已变化，拒绝续跑旧证据。')
            check(self.trace['script_sha256'] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
                  '总脚本已变化，请新建运行，不混用旧记录。')
            last = self.trace.get('formal_current')
            check(not last or tree_hashes(self.adopted) == last['manifest'], '正式版在阶段之间发生变化。')
        else:
            source = OUTPUT / 'adopted' / config.SKILL_NAME
            if source.exists():
                shutil.copytree(source, self.adopted)
                seed = {'source': str(source), 'source_manifest': tree_hashes(source)}
            else:
                init_adopted()
                seed = {'source': 'lifecycle.init_adopted 的既有基线', 'source_manifest': tree_hashes(self.adopted)}
            self.trace = {'run_id': run_id, 'started_at': time.time(), 'model': config.MODEL,
                          'base_url': config.BASE_URL, 'root': str(self.root), 'stages': {},
                          'nodes': {}, 'hash_bridges': {}, 'events': [], 'homes': [],
                          'protected_before': protected_files(), 'seed': seed,
                          'script_sha256': hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
                          'hash_semantics': {'file': 'SKILL.md 的 SHA-256；TaskRun 与 CandidateBundle',
                              'tree': 'tree_hashes 的 ensure_ascii=False、sort_keys=True JSON 的 SHA-256；EvalReport 与 AdoptionDecision',
                              'shared': '单技能为文件哈希，多技能为上游技能名到文件哈希的紧凑 JSON 汇总哈希'}}
            shutil.copyfile(SCRIPT, self.root / 'run_assembly.executed.py')
            version = self.version(self.adopted)
            self.trace['baseline_version'] = version
            self.trace['formal_current'] = identity(self.adopted)
            self.save()

    def save(self):
        write_json(self.path, self.trace)
        write_json(OUTPUT / 'assembly_trace.json', self.trace)

    def node(self, node_id, kind, data, evidence, dependencies=()):
        check(node_id not in self.trace['nodes'], f'记录 ID 重复：{node_id}')
        check(all(d in self.trace['nodes'] for d in dependencies), '记录的前置来源尚不存在。')
        path = Path(evidence)
        self.trace['nodes'][node_id] = {'type': kind, 'data': data, 'depends_on': list(dependencies),
            'evidence': str(path), 'evidence_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.save()
        return node_id

    def version(self, directory):
        value = identity(directory)
        node_id = 'version:' + value['skill_tree_hash']
        if node_id not in self.trace['nodes']:
            path = self.root / 'versions' / (value['skill_tree_hash'] + '.json')
            write_json(path, value)
            self.trace['hash_bridges'][value['skill_tree_hash']] = value
            self.node(node_id, 'SkillIdentity', value, path)
        return node_id

    def runtime(self, tag, order_id, prompt, dependencies=()):
        directory = self.current / tag
        directory.mkdir(parents=True)
        instance = 'asm-' + self.trace['run_id'] + '-' + tag
        home = OUTPUT / 'homes' / instance
        self.trace['homes'].append(str(home))
        self.save()
        request = {'instance': instance, 'home': str(home), 'evidence': str(directory),
                   'adopted': str(self.adopted), 'order_id': order_id, 'prompt': prompt,
                   'background_timeout': 240}
        write_json(directory / 'request.json', request)
        version = self.version(self.adopted)
        with (directory / 'console.log').open('w') as log:
            proc = subprocess.run([str(PYTHON), '-B', str(SCRIPT), '--worker', str(directory / 'request.json')],
                                  env=os.environ.copy(), cwd=config.REPO_ROOT, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=600)
        check(proc.returncode == 0 and (directory / 'result.json').exists(),
              f'{tag} 子进程失败，见 {directory / "console.log"}')
        result = read(directory / 'result.json')
        task_id = 'task:' + result['task']['session_id']
        self.node(task_id, 'TaskRun', result['task'], directory / 'foreground.json', (version, *dependencies))
        bg_id = 'background:' + result['task']['session_id']
        self.node(bg_id, 'BackgroundReviewResult', result['background'], directory / 'background_result.json', (task_id,))
        result.update(task_node=task_id, background_node=bg_id)
        return result

    def smoke(self):
        from assembly.eval.judge import judge_answer
        from assembly.eval.runner import load_cases
        result = self.runtime('smoke', 'ORD-B1', '请实际查询 ORD-B1，说明应付、每笔成功金额、成功合计和是否付清。')
        verdict = judge_answer(next(c for c in load_cases() if c['case_id'] == 'ORD-B1'), result['task']['answer'])
        write_json(self.current / 'business_check.json', verdict)
        check(verdict.business_correct, 'smoke 的实际业务结论没有通过既有评分器。')
        result['business_check'] = verdict.to_dict()
        return result

    def learn(self):
        from run_skillclaw_demo import POLICY
        result = self.runtime('learn', 'ORD-A2', POLICY + '\n请实际查询 ORD-A2，说明各金额、支付状态、受理与到账的区别、下一步及处理队列。')
        bg = result['background']
        check(bg['triggered'] and not bg['blocked_foreground'], '没有证明原生后台异步启动。')
        return result

    def candidate(self):
        from assembly.lifecycle.skills import create_candidate
        learned = self.trace['stages']['learn']['result']
        before = identity(self.adopted)
        skills = Path(learned['home']) / 'skills'
        sources = sorted(p for p in skills.rglob('SKILL.md') if '.archive' not in p.parts)
        changed = [p for p in sources if p.parent.name != config.SKILL_NAME
                   or hashlib.sha256(p.read_bytes()).hexdigest() != before['skill_file_hash']]
        canonical = skills / config.SKILL_NAME / 'SKILL.md'
        source = canonical if canonical in changed else (changed[0] if changed else None)
        changes, adapter = {}, None
        if source is not None and not learned['background']['failed']:
            for rel in tree_hashes(source.parent):
                changes[rel] = (source.parent / rel).read_text(encoding='utf-8')
            original = changes['SKILL.md']
            changes['SKILL.md'] = re.sub(r'(?m)^name:\s*[^\n]+$', 'name: ' + config.SKILL_NAME, original, count=1)
            adapter = {'source': str(source), 'source_manifest': tree_hashes(source.parent),
                       'source_hash': sha256_text(original), 'changed_field': '仅 frontmatter.name 对齐正式技能标识',
                       'adapted_hash': sha256_text(changes['SKILL.md'])}
        bundle = create_candidate(self.adopted, 'foreground_review', changes)
        after = identity(self.adopted)
        check(before == after, '候选创建改变了正式目录。')
        path = self.current / 'candidate.json'
        write_json(path, bundle)
        version = self.version(Path(bundle.candidate_dir))
        node = self.node('candidate:' + bundle.candidate_id, 'CandidateBundle', bundle.to_dict(), path,
                         (learned['task_node'], learned['background_node'], self.trace['baseline_version'], version))
        result = {'bundle': bundle.to_dict(), 'candidate_node': node, 'identity_adapter': adapter,
                  'baseline': before, 'candidate_identity': identity(Path(bundle.candidate_dir)),
                  'formal_before': before, 'formal_after': after, 'formal_unchanged': True,
                  'real_review_revision': bool(changes),
                  'note': '真实复盘产出经标识对齐成为候选' if changes else '后台未产生可用修订，仅复制基线；不宣称改善'}
        write_json(self.current / 'isolation.json', result)
        return result

    def install_adopted(self, bundle, decision):
        """模块缺少 adopt 入口；只作经双重哈希检查的整目录切换。"""
        candidate = Path(bundle.candidate_dir)
        before, target = identity(self.adopted), identity(candidate)
        check(decision.decision == 'ADOPT' and all(c['passed'] for c in decision.checks), '尚未获可信 ADOPT 决策。')
        check(before['skill_tree_hash'] == decision.previous_skill_hash and target['skill_tree_hash'] == decision.new_skill_hash,
              '采用时版本与评测决策不符。')
        check(before['skill_file_hash'] == bundle.base_skill_hash and target['skill_file_hash'] == bundle.candidate_hash,
              '采用时版本与 CandidateBundle 不符。')
        staged = self.adopted.parent / ('.adopt-stage-' + uuid.uuid4().hex)
        backup = self.adopted.parent / ('.adopt-backup-' + uuid.uuid4().hex)
        shutil.copytree(candidate, staged)
        check(tree_hashes(staged) == target['manifest'] and identity(self.adopted) == before, '采用准备期间发生并行改写。')
        os.replace(self.adopted, backup)
        try:
            os.replace(staged, self.adopted)
            check(tree_hashes(self.adopted) == target['manifest'], '采用后的目录校验失败。')
        except BaseException:
            if self.adopted.exists():
                shutil.rmtree(self.adopted)
            os.replace(backup, self.adopted)
            raise
        # 旧正式目录保留为本轮可审计材料，不删除。
        return {'backup': str(backup), 'before': before, 'after': identity(self.adopted)}

    def eval(self):
        from assembly.eval.judge import judge_self_check
        from assembly.eval.policy import HoldoutComparison, decide
        from assembly.eval.runner import evaluate, load_cases, run_answers, skill_tree_hash
        from run_eval_demo import HOLDOUT_PROMPTS
        source = self.trace['stages']['candidate']['result']
        bundle = CandidateBundle(**source['bundle'])
        before = identity(self.adopted)
        check(before == source['baseline'], '候选的基线已变，停止评测。')
        check(identity(Path(bundle.candidate_dir)) == source['candidate_identity'], '候选在评测前已变。')
        cases, holdout = load_cases(), copy.deepcopy(load_cases())
        for c in holdout:
            c['prompt'] = HOLDOUT_PROMPTS[c['case_id']]
        write_json(self.current / 'plan.json', {'main': cases, 'holdout': holdout,
            'baseline': before, 'candidate': source['candidate_identity'],
            'fixed_at': time.time(), 'search_after_scores': False,
            'scope': '四笔受控订单；留出组是相同订单的新问法，不是新订单泛化'})
        selfcheck = judge_self_check()
        write_json(self.current / 'selfcheck.json', selfcheck)
        reports, report_nodes = {}, []
        for label, directory, group in (
            ('baseline', self.adopted, cases), ('candidate', Path(bundle.candidate_dir), cases),
            ('baseline-holdout', self.adopted, holdout), ('candidate-holdout', Path(bundle.candidate_dir), holdout)):
            print('评测 ' + label, flush=True)
            answers = run_answers(directory, group)
            report = evaluate(bundle.candidate_id if label == 'candidate' else label,
                              skill_tree_hash(directory), group, answers)
            path = self.current / (label + '.json')
            write_json(path, report)
            reports[label] = report
            report_nodes.append(self.node('eval:' + report.evaluation_context['run_id'], 'EvalReport',
                report.to_dict(), path, (source['candidate_node'], self.version(directory))))
            check(identity(self.adopted) == before, '模型评测改变了正式目录。')
        decision = decide(reports['candidate'], reports['baseline'], selfcheck,
                          HoldoutComparison(reports['baseline-holdout'], reports['candidate-holdout']))
        write_json(self.current / 'decision.json', decision)
        node = self.node('decision:' + bundle.candidate_id, 'AdoptionDecision', decision.to_dict(),
                         self.current / 'decision.json', report_nodes)
        installation = self.install_adopted(bundle, decision) if decision.decision == 'ADOPT' else None
        after = identity(self.adopted)
        check(after['skill_tree_hash'] == decision.new_skill_hash, '执行后的正式版与决策不一致。')
        check(decision.decision == 'ADOPT' or before == after, 'REJECT 改变了正式目录。')
        self.trace['formal_current'] = after
        result = {'decision': decision.to_dict(), 'decision_node': node,
                  'formal_before': before, 'formal_after': after, 'installation': installation,
                  'only_adopt_changes_formal': decision.decision == 'ADOPT' or before == after,
                  'selfcheck_passed': selfcheck.passed,
                  'reports': {k: {'passed': r.passed, 'total': r.total, 'skill_hash': r.skill_hash,
                                  'run_id': r.evaluation_context['run_id'],
                                  'business_correct': sum(c.business_correct for c in r.cases)} for k, r in reports.items()}}
        check(all(r.evaluation_context['tool_grounded'] for r in reports.values()), '业务评测有调用未完成；不能把基础设施失败当候选业务失败。')
        write_json(self.current / 'execution.json', result)
        return result

    def shared(self):
        from assembly.skillclaw.server import runtime_paths, start_server, stop_server
        from assembly.skillclaw.client import upload_sessions, trigger_evolution, publish, pull_and_load, run_task
        from assembly.runtime.payment_tool import query_order_payments
        from run_skillclaw_demo import POLICY, OUTPUT_FORMAT, answer_json
        decision = self.trace['stages']['eval']['result']['decision_node']
        source_runs, sessions = [], []
        # 失败也保留已完成的真实步骤，供后续 snapshot/audit 如实汇报。
        shared_result = {'outcome': 'source_tasks_running', 'source_task_nodes': [],
                         'formal_before': identity(self.adopted)}
        self.trace['stages']['shared']['result'] = shared_result
        self.save()
        for tag, order in (('shared-A', 'ORD-A2'), ('shared-B', 'ORD-B2')):
            # 轻量 ValidationWorker 重放只收 prompt，不提供查询工具。
            # 在真实来源任务开始前附上原始快照，同时要求 Hermes 再实际查验。
            raw_order = query_order_payments(order)
            source = self.runtime(tag, order, POLICY + '\n订单原始只读快照：'
                                  + json.dumps(raw_order, ensure_ascii=False)
                                  + '\n实际查询订单' + order + '，核对快照与工具流水后处理。' + OUTPUT_FORMAT,
                                  (decision,))
            source_runs.append(source)
            shared_result['source_task_nodes'].append(source['task_node'])
            self.save()
            messages = read(Path(source['evidence']) / 'messages.json')
            # 只适配字段，逐条保留前台真正发生的 assistant 与 tool 消息。
            turns = []
            for index, message in enumerate(messages):
                if message.get('role') != 'assistant':
                    continue
                receipts = []
                for nxt in messages[index + 1:]:
                    if nxt.get('role') != 'tool':
                        break
                    receipts.append(nxt)
                calls = message.get('tool_calls') or []
                turns.append({'turn_num': len(turns) + 1,
                    'raw_turn_kind': 'tool_use' if calls else 'final',
                    'prompt_text': source['task']['prompt'], 'response_text': message.get('content') or '',
                    'tool_calls': calls, 'tool_results_raw': receipts, 'tool_results': receipts,
                    'tool_observations': [], 'tool_errors': [], 'read_skills': [], 'modified_skills': [],
                    'injected_skills': [config.SKILL_NAME], 'prm_score': None})
            sessions.append({'session_id': source['task']['session_id'], 'instance': source['task']['instance'],
                             'turns': turns})
        write_json(self.current / 'source_sessions.json', sessions)
        os.environ['ASSEMBLY_SKILLCLAW_RUN_ID'] = self.trace['run_id']
        server_home = OUTPUT / 'homes' / ('asm-' + self.trace['run_id'] + '-server')
        server_home.mkdir(parents=True)
        self.trace['homes'].append(str(server_home))
        os.environ['HERMES_HOME'] = str(server_home)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        endpoint = f'http://127.0.0.1:{port}'
        proc = start_server(port)
        shared_root = Path(runtime_paths()['root'])
        shared_result.update(shared_root=str(shared_root), outcome='server_started')
        self.save()
        try:
            c_instance = 'asm-' + self.trace['run_id'] + '-C'
            c_home = OUTPUT / 'homes' / c_instance
            check(not c_home.exists(), '第三实例必须是全新目录。')
            self.trace['homes'].append(str(c_home))
            heldout = {'amount_due': 1300, 'transactions': [
                {'txn_id': 'TXN-C1', 'amount': 500, 'status': 'success'},
                {'txn_id': 'TXN-C2', 'amount': 800, 'status': 'success'}],
                'merchant_notification': 'merchant_notification_missing'}
            prompt = ('总装支付演示系统：核实新订单 ORD-C3 付款状态并选择处理队列。数据：'
                      + json.dumps(heldout, ensure_ascii=False) + OUTPUT_FORMAT)
            os.environ['HERMES_HOME'] = str(c_home)
            baseline = run_task(c_home, prompt, instance=c_instance)
            write_json(self.current / 'c_before.json', baseline)
            check(not baseline['skill_hashes'] and not baseline['read_skills'], '第三实例基线混入既有技能。')
            uploaded = upload_sessions(endpoint, sessions)
            shared_result.update(uploaded_session_ids=uploaded, outcome='sessions_uploaded')
            self.save()
            check(set(uploaded) == {s['session_id'] for s in sessions}, '上传回执不对应源会话。')
            print('共享：真实演化与服务端验证', flush=True)
            evolution = trigger_evolution(endpoint)
            write_json(self.current / 'evolution.json', evolution)
            queued = [r for r in evolution.get('evolutions', []) if r.get('action') == 'queued_for_validation']
            shared_result.update(verification=[r.get('verification', {}) for r in queued],
                                 validation_job_ids=[r['validation_job_id'] for r in queued],
                                 outcome='queued_for_validation' if queued else 'server_verification_rejected')
            self.save()
            check(queued and all(r.get('verification', {}).get('accepted') is True for r in queued)
                  and not evolution.get('had_processing_error'), '没有通过服务端验证的共享候选。')
            print('共享：独立客户端重放，随后原生发布', flush=True)
            publication = publish(endpoint, evolution, validator_alias='assembly-validator-D')
            write_json(self.current / 'publication.json', publication)
            records = [r for r in publication['publication'].get('evolutions', [])
                       if r.get('action') == 'published_after_validation' and r.get('uploaded')]
            replays = [r for group in publication['results'].values() for r in group]
            manifest = publication['manifest']
            jobs = {r['validation_job_id'] for r in queued}
            shared_result.update(validation=publication.get('validation', {}),
                                 publication_records=records,
                                 replay_scores=[r.get('replay_summary') for r in replays],
                                 validation_decisions=publication.get('decisions', {}))
            has_errors = bool(shared_result['validation'].get('error_jobs')) or any(
                r.get('decision') == 'error' for r in replays)
            replay_accepted = bool(records and replays) and all(
                r.get('accepted') is True and r.get('validator_mode') == 'replay'
                and r.get('score') is not None and r['score'] >= .75
                and r.get('replay_summary', {}).get('cases') for r in replays)
            shared_result['outcome'] = ('validation_error' if has_errors else
                                        'validated' if replay_accepted else 'rejected')
            source_nodes = tuple(s['task_node'] for s in source_runs)
            publish_node = self.node('publication:' + self.trace['run_id'], 'SkillClawPublication', publication,
                                    self.current / 'publication.json', source_nodes)
            shared_result['publication_node'] = publish_node
            self.save()
            check(not has_errors and replay_accepted,
                  '共享候选验证错误，未计为通过。' if has_errors else
                  '共享候选未全部通过真实重放验证或尚未发布，停止第三实例加载。')
            check(all(set(r.get('session_ids', [])) == set(uploaded) and r.get('validation_job_id') in jobs
                  and publication['decisions'][r['validation_job_id']]['status'] == 'published'
                  and manifest[r['skill_name']]['version'] == r['version'] for r in records),
                  '发布记录缺少完整来源或验证决定。')
            loaded_hash, behavior = pull_and_load(endpoint, c_home, prompt)
            write_json(self.current / 'c_after.json', behavior)
            before_answer, after_answer = answer_json(baseline['answer']), answer_json(behavior['answer'])
            reads = behavior['read_skills']
            consumed = bool(reads) and all(r['sha256'] == manifest[r['skill_name']]['sha256'] for r in reads)
            changed = (consumed and before_answer.get('route') == 'unknown'
                       and after_answer.get('route') == 'merchant-notify-23'
                       and after_answer.get('paid_amount') == 1300 and after_answer.get('remaining') == 0)
            comparison = {'before': before_answer, 'after': after_answer, 'changed': changed,
                          'same_prompt': baseline['prompt'] == behavior['prompt'], 'reads': reads,
                          'loaded_hash': loaded_hash, 'manifest': manifest, 'home': str(c_home),
                          'instance_alias': c_instance, 'native_client_instance': behavior['instance']}
            write_json(self.current / 'comparison.json', comparison)
            revision = SharedRevision([s['instance'] for s in sessions], uploaded, True, True,
                ','.join(f"{r['skill_name']}@v{r['version']}" for r in records), c_instance, loaded_hash, changed)
            write_json(self.current / 'shared_revision.json', revision)
            node = self.node('shared:' + self.trace['run_id'], 'SharedRevision', revision.to_dict(),
                             self.current / 'shared_revision.json', (publish_node,))
            shared_result.update(revision=revision.to_dict(), shared_node=node, comparison=comparison,
                                 outcome='published_behavior_unconfirmed')
            self.save()
            check(changed and comparison['same_prompt'], '第三实例没有证明加载后改变新订单处理行为。')
            shared_result['outcome'] = 'published_and_consumed'
            return shared_result
        finally:
            stop_server(proc)
            shared_result['formal_after'] = identity(self.adopted)
            shared_result['formal_unchanged'] = shared_result['formal_before'] == shared_result['formal_after']
            self.save()

    def snapshot(self):
        from assembly.lifecycle.snapshots import snapshot, restore
        formal = identity(self.adopted)
        probe = self.root / 'adopted' / 'restore-probe'
        shutil.copytree(self.adopted, probe)
        # 支持文件和空目录在快照之前加入练习副本，不污染正式技能。
        (probe / 'references').mkdir(exist_ok=True)
        (probe / 'references' / 'restore-proof.txt').write_text('完整目录恢复核验\n', encoding='utf-8')
        (probe / 'binary.bin').write_bytes(bytes(range(256)))
        (probe / 'empty-dir').mkdir()
        saved = snapshot(probe, 'assembly-' + self.trace['run_id'])
        directories = sorted(p.relative_to(probe).as_posix() for p in probe.rglob('*') if p.is_dir())
        (probe / 'SKILL.md').write_text('本轮恢复演练的临时改动\n', encoding='utf-8')
        (probe / 'references' / 'restore-proof.txt').unlink()
        (probe / 'binary.bin').write_bytes(b'changed')
        (probe / 'empty-dir').rmdir()
        (probe / 'extra.txt').write_text('恢复时应移除\n', encoding='utf-8')
        changed = tree_hashes(probe)
        restored = restore(saved, probe)
        restored_dirs = sorted(p.relative_to(probe).as_posix() for p in probe.rglob('*') if p.is_dir())
        result = {'snapshot': saved.to_dict(), 'before_manifest_hash': digest(saved.manifest),
                  'changed_manifest_hash': digest(changed), 'restored_manifest_hash': digest(restored),
                  'restored_manifest': restored, 'matched': saved.manifest == restored,
                  'empty_directories_matched': directories == restored_dirs,
                  'extra_removed': not (probe / 'extra.txt').exists(),
                  'formal_before': formal, 'formal_after': identity(self.adopted),
                  'scope': '独立恢复练习副本；没有制造正式采用版的业务退化，也没有伪造 RESTORE 决策'}
        write_json(self.current / 'snapshot.json', result)
        node = self.node('snapshot:' + saved.label, 'Snapshot', result, self.current / 'snapshot.json',
                         (self.trace['stages']['eval']['result']['decision_node'],))
        check(result['matched'] and result['empty_directories_matched'] and result['extra_removed']
              and formal == result['formal_after'] and digest(changed) != digest(restored), '整目录恢复未通过。')
        result['snapshot_node'] = node
        return result

    def execute(self, stage):
        old = self.trace['stages'].get(stage)
        if old:
            check(old['status'] == 'passed', f'{stage} 已失败，保留原记录；请使用新的 run-id。')
            return
        for dependency in REQUIRES[stage]:
            self.execute(dependency)
        self.current = self.root / 'stages' / stage
        self.current.mkdir(parents=True, exist_ok=True)
        started = time.time()
        self.trace['events'].append({'kind': stage + '.start', 'at': started})
        print('开始 ' + stage, flush=True)
        entry = {'started_at': started, 'evidence': str(self.current), 'status': 'running'}
        self.trace['stages'][stage] = entry
        self.save()
        try:
            formal_before = identity(self.adopted)
            entry['result'] = getattr(self, stage)()
            if stage != 'eval':
                check(identity(self.adopted) == formal_before, f'{stage} 改变了正式目录。')
            entry['status'] = 'passed'
        except BaseException as exc:
            entry['status'] = 'failed'
            entry['error'] = {'type': type(exc).__name__,
                              'message': str(exc).replace(os.environ['GLM_API_KEY'], '[密钥已隐藏]')}
            raise
        finally:
            entry['elapsed_sec'] = time.time() - started
            self.trace['events'].append({'kind': stage + '.' + entry['status'], 'at': time.time()})
            self.trace['formal_current'] = identity(self.adopted)
            self.save()
            print(stage + '：' + entry['status'], flush=True)

    def audit(self):
        self.trace['protected_unchanged'] = self.trace['protected_before'] == protected_files()
        self.trace['script_unchanged'] = self.trace['script_sha256'] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
        changed_nodes = [i for i, n in self.trace['nodes'].items()
                         if not Path(n['evidence']).is_file() or hashlib.sha256(Path(n['evidence']).read_bytes()).hexdigest() != n['evidence_sha256']]
        self.trace['evidence_integrity'] = {'passed': not changed_nodes, 'changed_nodes': changed_nodes}
        # 将原始模型响应、工具回执、冻结技能、SQLite 与全部本轮资料一起校验。
        artifact_path = self.root / 'artifact_manifest.json'
        excluded = {self.path, self.root / 'run.lock', artifact_path, self.root / 'final.json'}
        files = set()
        for root in [self.root, *(Path(p) for p in self.trace['homes'])]:
            # 正式目录可在后续 eval 的 ADOPT 中变化；由 formal_current 和决策单独核验。
            files.update(p for p in root.rglob('*') if p.is_file() and p not in excluded
                         and not p.is_relative_to(self.adopted))
        manifest = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
        prior = read(artifact_path) if artifact_path.exists() else {}
        modified = [p for p, h in prior.items() if manifest.get(p) != h]
        if not modified:
            write_json(artifact_path, manifest)
        self.trace['raw_artifact_integrity'] = {'passed': not modified, 'checked_files': len(manifest),
            'manifest': str(artifact_path), 'modified_files': modified}
        self.trace['all_completed'] = all(
            self.trace['stages'].get(s, {}).get('status') in ('passed', 'failed') for s in STAGES)
        self.trace['all_passed'] = False
        self.save()
        self.render_report()
        key = os.environ['GLM_API_KEY'].encode()
        files.update(p for p in self.adopted.rglob('*') if p.is_file())
        # 包括本轮获准修改的适配层与新增封装，不能只扫描总入口和运行材料。
        changed_sources = {SCRIPT, *(config.ASSEMBLY_DIR / 'assembly' / 'skillclaw').glob('*.py')}
        files.update(changed_sources)
        files.update({OUTPUT / 'ASSEMBLY_REPORT.md', OUTPUT / 'assembly_trace.json', self.path, artifact_path})
        leaked = [str(p) for p in sorted(files) if key in p.read_bytes()]
        self.trace['secret_scan'] = {'passed': not leaked, 'file_count': len(files), 'matched_files': leaked,
                                     'source_files': sorted(str(p) for p in changed_sources),
                                     'method': '仅在内存比较当前 GLM_API_KEY 字节；不输出值'}
        audit_ok = (self.trace['protected_unchanged'] and self.trace['script_unchanged']
                    and not changed_nodes and not modified and not leaked)
        self.trace['audit_passed'] = bool(audit_ok)
        if not audit_ok:
            self.trace.pop('final_record', None)
        all_passed = audit_ok and all(self.trace['stages'].get(s, {}).get('status') == 'passed' for s in STAGES)
        self.trace['all_passed'] = bool(all_passed)
        if all_passed and 'final:' + self.trace['run_id'] not in self.trace['nodes']:
            stages = self.trace['stages']
            deps = [stages['smoke']['result']['task_node'], stages['learn']['result']['background_node'],
                    stages['shared']['result']['shared_node'], stages['snapshot']['result']['snapshot_node']]
            final = {'run_id': self.trace['run_id'], 'formal': identity(self.adopted),
                     'shared': stages['shared']['result']['revision'], 'all_passed': True}
            write_json(self.root / 'final.json', final)
            self.trace['final_record'] = self.node('final:' + self.trace['run_id'], 'AssemblyResult', final,
                                                   self.root / 'final.json', deps)
        self.save()
        self.render_report()
        # 补查最终汇总生成后的文件；失败时撤销当前通过标记，保留失败材料。
        final_files = [OUTPUT / 'ASSEMBLY_REPORT.md', OUTPUT / 'assembly_trace.json', self.path]
        if (self.root / 'final.json').exists():
            final_files.append(self.root / 'final.json')
        leaked += [str(p) for p in final_files if key in p.read_bytes()]
        if leaked:
            self.trace['all_passed'] = self.trace['audit_passed'] = False
            self.trace.pop('final_record', None)
            self.trace['secret_scan'].update(passed=False, matched_files=sorted(set(leaked)))
            self.save()
            self.render_report()
        check(not leaked, '新增证据发现密钥内容；未输出密钥，请检查扫描记录。')
        check(audit_ok,
              '只读来源或运行脚本/证据指纹发生变化。')

    def render_report(self):
        stages = self.trace['stages']
        def result(name):
            return stages.get(name, {}).get('result', {})
        def link(name, filename):
            directory = stages.get(name, {}).get('evidence')
            if not directory:
                return '尚未运行'
            path = Path(directory) / filename
            return f'[{filename}]({path.relative_to(OUTPUT).as_posix()})' if path.exists() else '尚无记录'
        smoke, learn, candidate, ev, shared, snap = (result(s) for s in STAGES)
        bg, task = learn.get('background', {}), learn.get('task', {})
        decision = ev.get('decision', {})
        before, after = ev.get('formal_before', {}), ev.get('formal_after', {})
        comparison = shared.get('comparison', {})
        validation = shared.get('validation', {})
        relative = self.root.relative_to(OUTPUT).as_posix()
        passed = self.trace.get('all_passed', False)
        completed = self.trace.get('all_completed', False)
        def shared_link(filename):
            root = shared.get('shared_root')
            path = Path(root) / filename if root else None
            return (f'[{filename}]({path.relative_to(OUTPUT).as_posix()})'
                    if path and path.is_file() else '尚无记录')
        lines = ['# 《自进化 Agent 工程实战》总装验收', '',
            f"本轮：`{self.trace['run_id']}`；六阶段结果：**{'全部通过' if passed else '尚未全部通过'}**。",
            f"本轮请求：`{self.trace.get('requested_stage', 'all')}`；单阶段模式只补必要前置阶段，未运行阶段不计为本轮通过。",
            f"六阶段均已结束：`{completed}`；审计通过：`{self.trace.get('audit_passed', False)}`。验证拒绝或网络错误均保留原因，不计为通过。",
            f'统一索引：[assembly_trace.json](assembly_trace.json)；[本轮不可混用的记录]({relative}/trace.json)。', '',
            '目标与授权：仅修复总编排与 SkillClaw publish 适配层的网络超时、有限重试和进程隔离，'
            '随后按本轮请求真实调用 glm-5.2。保留四模块的其他逻辑、公共配置、契约、场景、上游源码和旧 SUMMARY；不操作飞书。'
            '所有来源在本轮开始时记录指纹，结束时复核；本轮修改不混入旧运行。', '',
            '父进程仅把 config.OUTPUT_DIR 的内存值指向本轮目录，隔离旧验收资料。'
            '正式采用目录是本轮 adopted/payment-status-investigation；从既有已采用版整树复制。'
            '所有 Hermes 实例位于原 output/homes/<instance>，逐个独立子进程；密钥仅来自已加载的 GLM_API_KEY。', '',
            '```mermaid', 'flowchart LR', 'F[真实前台] --> L[原生 daemon 复盘] --> C[隔离候选]',
            'C --> E[评分器自检与成对评测] --> D[ADOPT 或 REJECT]',
            'D --> AB[两个来源实例] --> S[上传 演化 验证 发布] --> T[第三实例新订单]',
            'D --> R[独立副本快照与恢复]', '```', '',
            '| 六部分 | 结果及关键时间或分数 | 证据 | 哈希对应关系 |',
            '|---|---|---|---|',
            f"| 在线前台 | {stages.get('smoke', {}).get('status', '未运行')}；{smoke.get('task', {}).get('foreground_elapsed_sec', '未知')} 秒 | {link('smoke', 'smoke/foreground.json')} | TaskRun.skill_hash → 基线文件哈希 |",
            f"| 持久化 | {smoke.get('persistence', {})} | {link('smoke', 'smoke/persisted_messages.json')} | SQLite session_id 对应 TaskRun；最终答复和工具回执逐项确认 |",
            f"| 后台学习 | 前台返回 {task.get('foreground_elapsed_sec', '未知')} 秒；后台结束 {bg.get('finished_after_sec', '未知')} 秒；blocked_foreground={bg.get('blocked_foreground', '未知')}；failed={bg.get('failed', '未知')} | {link('learn', 'learn/observation.json')}、{link('learn', 'learn/background_result.json')} | 相同 session_id；后台结果只修改实例副本 |",
            f"| 技能生命周期 | {stages.get('candidate', {}).get('status', '未运行')}；真实复盘修订={candidate.get('real_review_revision', '未知')}；恢复一致={snap.get('matched', '未知')} | {link('candidate', 'isolation.json')}、{link('snapshot', 'snapshot.json')} | CandidateBundle 文件哈希 ↔ 整树版本映射；快照 manifest 前后相同 |",
            f"| 评测与版本 | 自检={ev.get('selfcheck_passed', '未知')}；决策={decision.get('decision', '未知')} | {link('eval', 'selfcheck.json')}、{link('eval', 'decision.json')}、{link('eval', 'execution.json')} | EvalReport 与 AdoptionDecision 使用整树哈希，candidate_id 保持一致 |",
            f"| 跨实例共享 | {stages.get('shared', {}).get('status', '未运行')}；结果={shared.get('outcome', '未知')}；route {comparison.get('before', {}).get('route', '未知')} → {comparison.get('after', {}).get('route', '未知')} | {link('shared', 'source_sessions.json')}、{link('shared', 'publication.json')}、{link('shared', 'comparison.json')} | 决策 → 两个 TaskRun → 上传会话 → validation_job_id → 发布 sha256 → SharedRevision.loaded_skill_hash；未发布时止于验证决定 |", '',
            '| 阶段 | 真实结果 | 总耗时（秒） |', '|---|---|---:|',
            *[f"| {name} | {stages.get(name, {}).get('status', '未运行')} | {stages.get(name, {}).get('elapsed_sec', '未知')} |" for name in STAGES], '',
            '## 五项验收', '',
            f"1. all 六阶段均已结束：{completed}；全部通过：{passed}。前台返回 {task.get('foreground_elapsed_sec', '未知')} 秒，后台结束 {bg.get('finished_after_sec', '未知')} 秒，从同一前台开始计时；blocked_foreground={bg.get('blocked_foreground', '未知')}。后台时刻为原生 target 退出；join 终止确认上界另存。",
            f"2. 候选评测与决策：{decision.get('decision', '尚未完成')}；完整 checks 在 decision.json，逐项列于下表。",
            f"3. 第三实例真实加载及行为变化：{comparison.get('changed', False)}；"
            + ('已在相同新订单、相同提示下证明模型真实读取已发布技能。' if comparison.get('changed') else
               '尚未证明；验证拒绝或错误时不发布、不加载，不能计为通过。'),
            f"4. 整目录恢复：{snap.get('matched', False)}；manifest 哈希 `{snap.get('before_manifest_hash', '未知')}` → `{snap.get('restored_manifest_hash', '未知')}`；空目录一致={snap.get('empty_directories_matched', False)}。",
            f"5. 正式目录只在 ADOPT 时变化：{ev.get('only_adopt_changes_formal', False)}；本次 `{decision.get('decision', '未知')}`。",
            f"   前：`{before.get('skill_tree_hash', '未知')}`；后：`{after.get('skill_tree_hash', '未知')}`。", '',
            '| 评测组 | 业务正确 | 全部关键点通过 |', '|---|---:|---:|']
        for label, r in ev.get('reports', {}).items():
            lines.append(f"| {label} | {r['business_correct']}/{r['total']} | {r['passed']}/{r['total']} |")
        lines += ['', '| 决策检查 | 通过 |', '|---|---|']
        lines += [f"| {c['name']} | {c['passed']} |" for c in decision.get('checks', [])]
        lines += ['', '## 哈希与来源', '',
                  '| 版本 | SKILL.md 文件哈希 | 整目录哈希 |', '|---|---|---|']
        for label, value in [('基线', candidate.get('baseline', {})), ('候选', candidate.get('candidate_identity', {})), ('最终正式版', after)]:
            lines.append(f"| {label} | `{value.get('skill_file_hash', '未知')}` | `{value.get('skill_tree_hash', '未知')}` |")
        lines += ['', f"共享加载哈希：`{shared.get('revision', {}).get('loaded_skill_hash', '未知')}`。共享演化是由源会话派生的新版本，不要求与本地候选相同。",
                  'assembly_trace.json 的 nodes 保存契约记录、证据文件指纹与 depends_on；全部通过时从 final_record 逐级回溯，未通过时从各阶段及已有节点查看已完成步骤和失败原因。hash_bridges 保留文件哈希、整树哈希和完整 manifest，不混淆两种口径。', '',
                  '## 共享验证与防挂起记录', '',
                  '修复依据、代码差异与本地故障注入：[超时修复记录](timeout-repair/README.md)。'
                  '旧采样可确认网络工作线程等待 SSL 读取，但不能区分该次是 chat 还是 PRM；本轮逐请求记录组件、case、分支与重试。',
                  f"共享结果：`{shared.get('outcome', '尚未运行')}`；检查 job 数={validation.get('checked_jobs', '未知')}，完成验证数={validation.get('validated_jobs', '未知')}，验证错误数={validation.get('error_jobs', '未知')}，跳过数={validation.get('skipped_jobs', '未知')}；原因：{validation.get('reason', '尚无记录')}。",
                  f"逐 job 子进程与超时记录：{shared_link('validation_worker.json')}；原始验证结果：{shared_link('validation_results.json')}；发布回执与完整诊断：{link('shared', 'publication.json')}。",
                  f"共享阶段正式目录未改变：`{shared.get('formal_unchanged', '未知')}`。网络持续失败记为验证错误，不按业务失败打分；拒绝与验证错误均不能使候选被发布或由第三实例加载。",
                  '回放的 GLM chat 与 PRM 评分由 publish 适配层在子进程内设置单次 240 秒总超时、最多两次重试，退避 1、2 秒；每个 validation job 最长 600 秒，期限覆盖全部 case、chat、评分和重试，可提前截断重试预算。超时终止并回收子进程。shared 返回后进入 audit；all 模式还继续 snapshot。', '',
                  '## 覆盖范围与边界', '',
                  '- 各项真实执行结果以上表与证据为准；未完成步骤不会因报告存在而计为通过。订单为已有 scenarios 受控教学数据，不是生产支付查询。',
                  '- 评测沿用现有最小 GLM 工具循环；主集四单，holdout 为相同四单的新问法。四份运行相互独立，评分规则未改；单轮结果不证明统计泛化。',
                  '- 共享 A/B 使用本轮 Hermes 真实任务；C 采用既有 SkillManager 与轻量工具 Agent，只有验证通过并发布后才拉取及读取 SKILL.md；是否完成以 comparison.json 为准。尚未覆盖完整 Hermes 的共享技能发现与消费。共享使用上游 LocalObjectStore，未覆盖远程 OSS 与跨主机并发。',
                  '- 上游轻量重放没有支付工具，所以来源任务在实际调用开始前含原始订单快照，Hermes 仍实际调用查询核对；上传保留原提示与真实工具回执。C 原生客户端标签为 C，报告另外记录它的唯一 home 实例别名。',
                  '- 服务端 verifier 和客户端重放均使用 glm-5.2；独立调用不等于独立厂商或人工审查。内部 route 是演示规程，仅来源任务给出，C 提示不含答案。',
                  '- 本轮选择真实后台复盘接入候选；没有额外运行 Curator 或 GEPA，也没有声称每轮必有提升。REJECT 后仍可从保留的正式版分享真实任务经验。',
                  '- 背景复盘是 daemon。验收在前台返回后等待结束以保留材料；不能由此推断进程异常退出时也能可靠完成。失败/超时留档，前台成功独立核对。',
                  '- 模块没有 adopt()；入口仅补充哈希检查和整目录切换。两次 rename 不构成断电事务，旧正式目录备份留存；同一 run-id 通过文件锁串行执行。',
                  '- 快照恢复在独立副本真实进行（修改主文件、删除嵌套文件、改二进制、增加多余文件和删除空目录）。未制造已采用版业务退化，因此没有虚构 RESTORE 决策。',
                  '- 上游会话上传使用内部方法；runtime 的技能哈希本身不能证明读取，本入口另外保留实际注入的文件正文与消息。后台模块已旁录上游 failed 返回值未被原生警告覆盖的情况。', '',
                  '## 复跑与审计', '', '```bash', 'set -a', 'source ~/.hermes/.env', 'set +a',
                  'unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY',
                  '.deps/hermes-agent/.venv/bin/python -B examples/assembly/run_assembly.py all',
                  '# 单阶段会先运行缺少的前置阶段；--run-id 可续用同一版本的成功结果',
                  '.deps/hermes-agent/.venv/bin/python -B examples/assembly/run_assembly.py eval', '```', '',
                  f"只读来源前后一致：`{self.trace.get('protected_unchanged', '未核验')}`；契约记录指纹：`{self.trace.get('evidence_integrity', {})}`；原始运行材料：`{self.trace.get('raw_artifact_integrity', {})}`；密钥扫描：`{self.trace.get('secret_scan', '未完成')}`。", '']
        earlier = [p for p in (OUTPUT / 'assembly-runs').glob('*/integration_stop.json') if p.parent != self.root]
        if self.trace.get('report_post_run_fix'):
            lines += ['报告生成文字在运行结束后作了局部校正，避免在共享验证失败时误称已加载。'
                      '阶段执行与原始结果未改、未重跑；运行时入口源码保留于 '
                      f'[{relative}/run_assembly.executed.py]({relative}/run_assembly.executed.py)。'
                      '具体源码哈希与校正范围见 trace.json 的 report_post_run_fix。', '']
        if earlier:
            lines += ['先前集成调试记录：']
            lines += [f"- [{p.parent.name}]({p.relative_to(OUTPUT).as_posix()})：{read(p)['reason']}" for p in earlier]
            lines.append('')
        for stage, entry in stages.items():
            if entry.get('error'):
                lines += [f"未完成项 `{stage}`：{entry['error']}。原始材料保留，未计为通过。", '']
        (OUTPUT / 'ASSEMBLY_REPORT.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', nargs='?', choices=('all', *STAGES), default='all')
    parser.add_argument('--run-id', help='继续指定运行；不覆盖失败尝试，不按分数补跑')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    require_environment()
    if args.worker:
        worker(args.worker)
        return 0
    run_id = args.run_id or time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    assembly = Assembly(run_id)
    assembly.trace['requested_stage'] = args.stage
    assembly.save()
    failed = False
    try:
        for stage in STAGES if args.stage == 'all' else (args.stage,):
            try:
                assembly.execute(stage)
            except Exception as exc:
                failed = True
                print(f'{stage} 未完成：{type(exc).__name__}；详见本轮 trace.json', flush=True)
    finally:
        assembly.audit()
    print('报告：' + str(OUTPUT / 'ASSEMBLY_REPORT.md'), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
