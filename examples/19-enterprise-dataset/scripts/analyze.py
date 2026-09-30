#!/usr/bin/env python3
"""按本轮原生报告配对；执行故障、模型完成和业务评分分别计数。"""
import hashlib
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from grader import grade

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('LECTURE19_OUTPUT_DIR', str(ROOT / 'output/demo'))).resolve()


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def relative(path):
    return str(path.relative_to(OUT))


def current_report(variant, case_ids, protocol_time):
    """拒绝旧报告、半成品、重复题目和不属于本次原生调用的事件。"""
    path = OUT / 'skill-up' / variant / 'iteration-1/report.json'
    report = load(path)
    start, end = stamp(report['start_time']), stamp(report['end_time'])
    if start < protocol_time - 1 or end < start:
        raise RuntimeError(f'{variant} 报告不属于当前 protocol，不能混用旧结果')
    results = report['case_results']
    index = {row['case_id']: row for row in results}
    if len(results) != len(index) or set(index) != case_ids:
        raise RuntimeError(f'{variant} 原生报告存在缺题、重复题或额外题')
    event_path = OUT / f'skill-up-{variant}-events.jsonl'
    all_events = [json.loads(line) for line in event_path.read_text().splitlines() if line.strip()]
    starts = [e for e in all_events if e['event'] == 'run_started' and abs(stamp(e['time'])-start) < 1]
    if len(starts) != 1:
        raise RuntimeError(f'{variant} 无法唯一对应原生事件调用')
    invocation = starts[0]['invocation_id']
    events = [e for e in all_events if e['invocation_id'] == invocation]
    finishes = [e for e in events if e['event'] == 'run_finished']
    completed = [e for e in events if e['event'] == 'case_completed']
    if len(finishes) != 1 or len(completed) != len(case_ids) or {e['payload']['case_id'] for e in completed} != case_ids:
        raise RuntimeError(f'{variant} 原生事件尚未完整结束')
    windows = {}
    for cid in case_ids:
        begins = [e for e in events if e['event'] == 'case_started' and e['payload']['case_id'] == cid]
        ends = [e for e in completed if e['payload']['case_id'] == cid]
        if len(begins) != 1 or len(ends) != 1 or ends[0]['payload']['status'] != index[cid]['status']:
            raise RuntimeError(f'{variant}/{cid} 原生事件与报告不一致')
        windows[cid] = (stamp(begins[0]['time']), stamp(ends[0]['time']))
    return report, index, windows, dict(report=relative(path), report_sha256=sha(path), events=relative(event_path),
                                      events_sha256=sha(event_path), invocation_id=invocation,
                                      start_time=report['start_time'], end_time=report['end_time'])


def grading_result(case, oracle):
    grading = case.get('grading') or {}
    assertions = grading.get('assertion_results', [])
    execution = next((a for a in assertions if a['text'] == 'expect.exit_code'), None)
    script = next((a for a in assertions if a['text'].startswith('script:')), None)
    completed = case['status'] in {'PASS', 'FAIL'} and execution is not None and execution.get('passed') is True
    # 复核保存下来的本次回答，只作可追溯诊断，不覆盖原生评分。
    recheck = grade(case.get('response', ''), oracle, 0) if completed else None
    if recheck is not None and script is not None and bool(script.get('passed')) != recheck['passed']:
        raise RuntimeError(f'{case["case_id"]} 原生评分与保存的回答复核不一致')
    score = int(case['status'] == 'PASS') if completed and script is not None else None
    error = case.get('error') or ''
    exit_match = re.search(r'exit(?: code)?\s+(\d+)', error)
    errors = recheck['errors'] if recheck is not None else [error or '执行未完成或缺少退出状态']
    if completed and script is None:
        errors = ['缺少原生业务评分断言；回答复核不能代替原生评分']
    return dict(score=score, execution_completed=completed,
                reported_exit_code=0 if completed else int(exit_match.group(1)) if exit_match else None,
                failure_kind='business' if completed and score == 0 else 'execution' if not completed else 'grading' if script is None else None,
                errors=errors,
                native_assertion_pass_rate=(grading.get('summary') or {}).get('pass_rate'),
                native_assertions=assertions, answer_recheck=recheck)


def execution_evidence(item, window, paths):
    """按本题时间段与 request_id 绑定预载和真实 API 记录，不伪造工具调用。"""
    matches = []
    for path in paths:
        if not window[0] <= path.stat().st_mtime <= window[1] + 0.1:
            continue
        data = load(path)
        if data.get('request_id') != item['request_id']:
            continue
        state = data.get('upstream_state', {})
        terminal_success = state.get('completed') is True and not any(state.get(k) for k in ['failed', 'interrupted', 'partial'])
        matches.append(dict(path=relative(path), sha256=sha(path), method=data['method'],
                            model_tools=data['tool_names'], model_tool_calls=data['model_tool_calls'],
                            adapter_table_reads=data['adapter_table_reads'], skill_sha256=data['skill_sha256'],
                            verified=data['verified'], upstream_state=state, terminal_success=terminal_success,
                            model_response_evidenced=data.get('successful_http_responses', 0) > 0,
                            api_requests_attempted=data.get('api_requests_attempted', 0),
                            api_responses_received=data.get('api_responses_received', 0),
                            strict_tool_allowlist=data['tool_names'] == []))
    if len(matches) > 1:
        raise RuntimeError(f'{item["case_id"]} 本题时间段有多份记录，不能自动择优')
    return matches[0] if matches else None


def main():
    manifest = load(ROOT / 'manifest.json')
    protocol_path = OUT / 'protocol.json'
    protocol = load(protocol_path)
    selected = protocol.get('selected_case_ids')
    if selected:
        manifest['cases'] = [item for item in manifest['cases'] if item['case_id'] in selected]
    case_ids = {item['case_id'] for item in manifest['cases']}
    if len(case_ids) != len(manifest['cases']):
        raise RuntimeError('manifest 出现重复题目')
    source_matches = {name: sha(ROOT / name) == digest for name, digest in protocol['files_sha256'].items()}
    if not all(source_matches.values()):
        raise RuntimeError('运行后受测文件变化，须保留原版本再分析：' + str([k for k, v in source_matches.items() if not v]))
    reports, indices, windows, sources = {}, {}, {}, {}
    for variant in ['legacy', 'hermes']:
        reports[variant], indices[variant], windows[variant], sources[variant] = current_report(variant, case_ids, protocol_path.stat().st_mtime)
    native_path = OUT / 'native-run-status.json'
    native = load(native_path)
    if native_path.stat().st_mtime < max(stamp(r['end_time']) for r in reports.values()):
        raise RuntimeError('套件尚未写入本轮 native-run-status，不能使用旧状态')
    if any(v['returncode'] for v in native['validations']):
        raise RuntimeError('本轮原生 YAML 校验未通过')
    paths = list((OUT / 'runtime').glob('isolated-*/execution-evidence.json'))
    rows = []
    for item in manifest['cases']:
        cid = item['case_id']
        a, b = indices['legacy'][cid], indices['hermes'][cid]
        oracle = load(ROOT / 'evals/grader-only' / f'{cid}.json')
        ag, bg = grading_result(a, oracle), grading_result(b, oracle)
        evidence = execution_evidence(item, windows['hermes'][cid], paths)
        live_completed = bool(evidence and evidence['terminal_success'] and evidence['verified'] and bg['execution_completed'])
        rows.append(dict(case_id=cid, stratum=item['stratum'], legacy_status=a['status'], legacy_score=ag['score'],
                         hermes_status=b['status'], hermes_score=bg['score'],
                         paired_difference=bg['score']-ag['score'] if bg['score'] is not None and ag['score'] is not None else None,
                         infrastructure_failure=not bg['execution_completed'], credential_or_endpoint_blocked=bg['reported_exit_code'] == 78,
                         legacy_grading=ag, hermes_grading=bg, hermes_model_started=bool(evidence and evidence['model_response_evidenced']), hermes_live_completed=live_completed, execution_evidence=evidence,
                         legacy_response=a.get('response'), hermes_response=b.get('response'), expected_orders=oracle['orders'],
                         source_trace_id=item['source_trace_id'], request_id=item['request_id'],
                         legacy_duration_ms=a.get('duration_ms'), hermes_duration_ms=b.get('duration_ms')))
    differences = [r['paired_difference'] for r in rows if r['paired_difference'] is not None]
    full = len(differences) == len(rows)
    mean = sum(differences) / len(differences) if full else None
    lower = max(-1, mean-math.sqrt(2*math.log(1/protocol['one_sided_alpha'])/len(rows))) if full else None
    legacy_passed = sum(r['legacy_score'] == 1 for r in rows)
    hermes_passed = sum(r['hermes_score'] == 1 for r in rows)
    scored = sum(r['hermes_score'] is not None for r in rows)
    started = sum(r['hermes_model_started'] for r in rows)
    completed = sum(r['hermes_live_completed'] for r in rows)
    if not full:
        conclusion = '存在执行故障或评分缺失，数据不足，保留原代码服务'
    elif mean < 0:
        conclusion = '本批教学题候选通过率低于旧服务；未支持替换，保留原代码服务'
    elif lower < -protocol['noninferiority_margin']:
        conclusion = '本批教学题候选通过率不低于旧服务，但未证明非劣效，保留原代码服务'
    else:
        conclusion = '教学样本计算达到预定界限，仍不足以支持生产替换，保留原代码服务'
    strata = []
    for name in dict.fromkeys(r['stratum'] for r in rows):
        group = [r for r in rows if r['stratum'] == name]
        strata.append(dict(stratum=name, independent_cases=len(group), legacy_pass=sum(r['legacy_score'] == 1 for r in group),
                           candidate_completed=sum(r['hermes_live_completed'] for r in group), candidate_pass=sum(r['hermes_score'] == 1 for r in group),
                           differences=[r['paired_difference'] for r in group]))
    isolation, selftest = (load(OUT / name) for name in ['tool-isolation.json', 'grader-selftest.json'])
    gateway_path=OUT/'gateway-demo.json'
    gateway=load(gateway_path) if gateway_path.exists() else {}
    rejected = [json.loads(line) for line in (ROOT / 'rejected.jsonl').read_text().splitlines() if line.strip()]
    limits = [f'{len(rows)} 个独立教学用例，每题预定一次；不是代表性生产样本，不用于生产替换决策。',
              '原生 assertion pass_rate 包含退出码和业务评分两项，不能直接当成业务通过率。',
              '模型启动数依据本题时间段的成功 HTTP 响应；完成数还要求上游完成状态和零退出码。启动后超时单列，执行故障不记成业务答错。',
              'Langfuse 导出为教学构造，未完成真实租户采集。',
              '没有真实用户结果，本地 HTTP 分组与演示事件不能证明线上业务非劣效。']
    gateway_current = gateway_path.exists() and gateway_path.stat().st_mtime >= protocol_path.stat().st_mtime
    if not gateway_current:
        limits.append('本轮未完成可核对的本地网关演示；逐题配对仍独立保存，不是线上试验。')
    if completed < len(rows):
        limits.append(f'{len(rows)-completed} 题尚无完整模型完成依据；详见逐题执行状态，不能宣称全部跑通。')
    summary = dict(teaching_only=True, dataset_version=manifest['dataset_version'], input_records=12+len(rejected), source_accepted_cases=12,
                   accepted_cases=len(rows), rejected_cases=len(rejected), legacy_passed=legacy_passed, legacy_total=len(rows),
                   legacy_pass_rate=legacy_passed/len(rows), hermes_passed=hermes_passed,
                   hermes_scored_cases=scored,
                   hermes_business_pass_rate_among_scored_cases=hermes_passed/scored if scored else None,
                   hermes_business_pass_rate=hermes_passed/len(rows) if full else None,
                   hermes_model_runs=started, hermes_model_started=started, hermes_model_completed=completed,
                   model_count_definitions='model_runs/model_started 以本题成功 HTTP 响应为依据；model_completed 另要求正常完成，不能把超时写成未调用模型。',
                   hermes_model_response_evidenced=started,
                   hermes_blocked=sum(r['credential_or_endpoint_blocked'] for r in rows),
                   hermes_execution_failures=sum(r['infrastructure_failure'] for r in rows),
                   paired_cases_available=len(differences), mean_difference=mean, conservative_lower_bound=lower,
                   margin=protocol['noninferiority_margin'], offline_conclusion=conclusion, production_noninferiority_proven=False,
                   current_service='保留原代码服务', glm_live_acceptance=completed == len(rows),
                   langfuse_collection_acceptance=protocol.get('langfuse_collection_acceptance', False), online_business_acceptance=False,
                   local_gateway_chain_passed=bool(gateway.get('stable_assignment_passed') and gateway.get('mismatched_trace_rejected')),
                   local_gateway_verified_this_run=gateway_current, grader_selftest_passed=selftest['passed'],
                   os_isolation_passed=isolation['os_sandbox_passed'], native_run_status=native,
                   source_files_unchanged=source_matches, run_sources=sources, per_case=rows, strata=strata,
                   script_sha256={str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / 'scripts').glob('*.py'))}, limits=limits)
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (OUT / 'paired-differences.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n')
    md = ['# 逐题配对结果', '', f'旧服务 {legacy_passed}/{len(rows)} 通过；Hermes 有真实模型启动依据 {started}/{len(rows)} 题、完整完成依据 {completed}/{len(rows)} 题，业务通过 {hermes_passed}/{len(rows)} 题。',
          f'可评分的 Hermes 回答中 {hermes_passed}/{scored} 通过；这只描述已完成题，不能代替完整样本比较。',
          '执行故障的业务分数与配对差留空；业务分数按整题所有硬字段是否通过取 0 或 1。', '',
          '| 用例 | 类型 | 旧服务 | Hermes | 差值 | 评分或执行原因 |', '|---|---|---|---|---:|---|']
    for row in rows:
        reason = ('；'.join(row['hermes_grading']['errors']) or '全部硬字段通过').replace('|', '\\|').replace('\n', ' ')
        diff = '未得出' if row['paired_difference'] is None else f'{row["paired_difference"]:+d}'
        md.append(f'| {row["case_id"]} | {row["stratum"]} | {row["legacy_status"]} | {row["hermes_status"]} | {diff} | {reason} |')
    mean_text = f'{mean:.6f}' if mean is not None else '未计算（存在缺失结果）'
    lower_text = f'{lower:.6f}' if lower is not None else '未计算（存在缺失结果）'
    md += ['', f'离线结论：{conclusion}。', f'完整配对 {len(differences)}/{len(rows)}；平均差值 {mean_text}；单侧 Hoeffding 保守下界 {lower_text}；预定非劣效界限 {-protocol["noninferiority_margin"]}。', '']
    md += ['- '+limit for limit in limits]
    md += ['', '原生报告、事件调用编号及哈希见 `summary.json` 的 `run_sources`；每题回答、评分断言和预载/模型运行记录路径见 `paired-differences.json`。']
    (OUT / 'paired-differences.md').write_text('\n'.join(md)+'\n')
    print(json.dumps({k: summary[k] for k in ['legacy_passed', 'hermes_passed', 'hermes_model_started', 'hermes_model_completed', 'hermes_blocked', 'hermes_execution_failures', 'offline_conclusion']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
