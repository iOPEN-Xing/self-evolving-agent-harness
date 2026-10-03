"""共享值守运行时。场景的未来数据和评测答案不会传给模型。"""
import copy
import json
import time
from pathlib import Path

SCENARIOS = Path(__file__).with_name('scenarios.json')
ALLOWED = {'PATROLLING': {'INVESTIGATING', 'SHIFT_ENDED'},
           'INVESTIGATING': {'VERIFYING', 'SHIFT_ENDED'},
           'VERIFYING': {'PATROLLING', 'SHIFT_ENDED'}, 'SHIFT_ENDED': set()}


def read_observation(scenario_id, logical_time):
    """只返回指定时刻；调用工具另有当前时钟检查，禁止访问未来或其他场景。"""
    data = json.loads(SCENARIOS.read_text())
    return copy.deepcopy(data[scenario_id]['snapshots'][str(logical_time)])


def poll_sensor(observation, memory):
    """仅按当前观测与已见历史产生事件，不解释根因。"""
    o, m = observation, observation['metrics']
    fresh = o['available'] and o['observed_at'] == o['window']['end']
    values = [m['overall'], *m['instances'].values()]
    alarm = fresh and any(x['timeout_rate'] > .05 for x in values)
    normal = fresh and bool(m['instances']) and all(
        x['timeout_rate'] < .01 and x['pool_waiting'] == 0 for x in values)
    event = {'type': 'OBSERVATION', 'service': o['service'], 'window': o['window'],
             'fresh': fresh, 'active_incident': memory.get('active', False)}
    if alarm:
        event['type'] = 'ALERT' if not memory.get('active') else 'ALERT_ONGOING'
        memory.update(active=True, normal_start=None, normal_end=None)
    elif normal and memory.get('active'):
        start, end = o['window']['start'], o['window']['end']
        if memory.get('normal_end') != start:
            memory['normal_start'] = start
        memory['normal_end'] = end
        duration = end - memory['normal_start']
        event['normal_minutes'] = duration
        event['type'] = 'RECOVERY' if duration >= 10 else 'RECOVERY_PENDING'
        # RECOVERY 只是观测事件，状态机仍要求模型复查和实际指标查询。
    else:
        event['type'] = 'NORMAL' if normal else 'OBSERVATION_UNKNOWN'
        memory.update(normal_start=None, normal_end=None)
    event['active_incident'] = memory.get('active', False)
    return event


TOOL_SCHEMAS = [
    {'type': 'function', 'function': {
        'name': name, 'description': desc,
        'parameters': {'type': 'object', 'properties': {
            'service': {'type': 'string'}, 'logical_time': {'type': 'integer'},
            **({'scope': {'type': 'string', 'enum': ['overall', 'instances']}} if name == 'read_metrics' else {})},
            'required': ['service', 'logical_time'] + (['scope'] if name == 'read_metrics' else []),
            'additionalProperties': False}}}
    for name, desc in (
        ('check_deployment', '只读查询当前服务部署状态与处置回执，回执不等于恢复。'),
        ('read_config', '只读查询当前配置文件与运行实例生效配置。'),
        ('read_metrics', '只读查询当前窗口整体或逐实例指标，含超时率、连接池和上游超时。'),
        ('query_logs', '只读查询当前窗口服务日志。'))]


class ObservationTools:
    def __init__(self, scenario_id, logical_time, audit):
        self.scenario_id, self.logical_time, self.audit = scenario_id, logical_time, audit

    def __call__(self, name, arguments):
        allowed = {'service', 'logical_time'} | ({'scope'} if name == 'read_metrics' else set())
        if name not in {t['function']['name'] for t in TOOL_SCHEMAS} or set(arguments) != allowed:
            raise ValueError('未知工具或参数不完整')
        if type(arguments['logical_time']) is not int or arguments['logical_time'] != self.logical_time:
            raise ValueError('只允许读取当前逻辑时刻，不能读取其他时刻快照')
        o = read_observation(self.scenario_id, self.logical_time)
        if arguments['service'] != o['service']:
            raise ValueError('服务对象不符')
        payload = {k: o[k] for k in ('service', 'window', 'observed_at', 'available')}
        if name == 'read_metrics':
            scope = arguments['scope']
            if scope not in ('overall', 'instances'):
                raise ValueError('指标范围不合法')
            payload.update(scope=scope, data=o['metrics'][scope])
        else:
            payload['data'] = o[{'check_deployment': 'deployment', 'read_config': 'config', 'query_logs': 'logs'}[name]]
        self.audit.append({'tool': name, 'arguments': arguments, 'observation': payload})
        return payload


def transition(state, target, reason, record_ref, events_path, logical_time):
    before = state['state']
    if target not in ALLOWED[before]:
        raise ValueError(f'非法状态转换 {before} -> {target}')
    row = {'run_id': state['run_id'], 'shift_id': state['shift_id'],
           'incident_id': state.get('incident_id'), 'from': before, 'to': target,
           'reason': reason, 'record_ref': record_ref, 'logical_time': logical_time}
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open('a') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    state['state'] = target
    state['transitions'].append(row)
    return row


def run_shift(*, scenario_id, ticks, run_turn, run_id, shift_id, session_id,
              events_path, state=None, investigation_record=None, end_shift=False,
              max_patrol_rounds=8, investigation_budget=40, user_alias='instance-A'):
    """一段会话可跨多轮；续班必须显式接收上一段调查记录。"""
    ticks = list(ticks)
    if (not ticks or any(type(t) is not int or t < 0 for t in ticks)
            or any(a >= b for a, b in zip(ticks, ticks[1:]))):
        raise ValueError('班次需要非空且严格递增的非负整数时钟')
    if any(type(n) is not int or n <= 0 for n in (max_patrol_rounds, investigation_budget)):
        raise ValueError('执行预算必须是正整数')
    state = copy.deepcopy(state) if state else dict(
        run_id=run_id, shift_id=shift_id, state='PATROLLING', incident_id=None,
        sensor={}, transitions=[], records=[], patrol_rounds=0, investigation_calls=0,
        service_recovered=False, open_questions=[])
    if state['shift_id'] != shift_id or state['run_id'] != run_id or state['state'] == 'SHIFT_ENDED':
        raise ValueError('班次身份或续班状态不符')
    if state['records'] and investigation_record != state['records']:
        raise ValueError('续班缺少完整调查记录')
    if state['records'] and ticks[0] <= state['records'][-1]['logical_time']:
        raise ValueError('续班时钟不能倒退或重复，不能重放恢复窗口')
    session = dict(session_id=session_id, run_id=run_id, shift_id=shift_id,
                   incident_id=state['incident_id'], user_alias=user_alias,
                   source='real_glm_toolloop', timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                   expected_turns=len(ticks), turns=[], received_investigation=investigation_record or [])
    messages = None
    for tick in ticks:
        if state['patrol_rounds'] >= max_patrol_rounds or state['investigation_calls'] >= investigation_budget:
            session['budget_exhausted'] = True
            break
        if state['state'] == 'PATROLLING':
            state['patrol_rounds'] += 1
        observed = read_observation(scenario_id, tick)
        event = poll_sensor(observed, state['sensor'])
        ref = f'{session_id}/turn-{len(session["turns"])+1}'
        if event['type'] == 'ALERT' and state['state'] == 'PATROLLING':
            count = sum(r['to'] == 'INVESTIGATING' for r in state['transitions']) + 1
            state['incident_id'] = f'{shift_id}-incident-{count}'
            transition(state, 'INVESTIGATING', 'Sensor 超时阈值告警', ref, events_path, tick)
        audit = []
        tools = ObservationTools(scenario_id, tick, audit)
        investigating = state['state'] != 'PATROLLING'

        def dispatch(name, arguments):
            # 按实际请求收费，不能相信适配器事后报告的 tool_calls 数量。
            # 参数不合法的调查请求也占一次预算；超限请求在读取前拒绝。
            if investigating:
                if state['investigation_calls'] >= investigation_budget:
                    session['budget_exhausted'] = True
                    raise ValueError('调查工具预算已耗尽')
                state['investigation_calls'] += 1
            return tools(name, arguments)
        context = dict(run_id=run_id, shift_id=shift_id, incident_id=state['incident_id'],
                       state=state['state'], logical_time=tick, service=observed['service'],
                       recovery_window_minutes=10,
                       event=event, prior_records=copy.deepcopy(state['records']),
                       investigation_budget_remaining=investigation_budget-state['investigation_calls'])
        prompt = ('请执行本轮值守：按当前 Skill 自行选择只读查询，给出有观测依据的判断与下一状态建议。'
                  '时间单位为逻辑分钟，窗口由教学时钟模拟推进。\n' + json.dumps(context, ensure_ascii=False))
        turn = run_turn(prompt, ref, dispatch, context, messages)
        messages = turn['messages']
        turn.update(turn_num=len(session['turns'])+1, run_id=run_id, shift_id=shift_id,
                    incident_id=state['incident_id'], phase_context=context, observation_reads=audit)
        if session.get('budget_exhausted'):
            turn['stop_reason'] = 'investigation_budget_exhausted'
            turn['assessment'] = None
        decision = turn.get('assessment') or {}
        requested = decision.get('next_state')
        denied = None
        metrics_read = any(r['tool'] == 'read_metrics' for r in audit)
        if requested and requested != state['state']:
            if (state['state'] == 'INVESTIGATING' and requested == 'VERIFYING'
                    and metrics_read and turn['stop_reason'] == 'final_answer'):
                transition(state, requested, '完成本轮调查，进入持续复查', ref, events_path, tick)
            elif (state['state'] == 'VERIFYING' and requested == 'PATROLLING'
                  and event['type'] == 'RECOVERY' and metrics_read
                  and turn['stop_reason'] == 'final_answer'
                  and decision.get('assessment') == 'recovered'):
                transition(state, requested, '连续新鲜观测满足恢复窗口，模型完成指标复查', ref, events_path, tick)
                state['sensor']['active'] = False
            else:
                denied = f'当前观测与状态不支持 {state["state"]} -> {requested}'
        # 服务恢复由实际状态转换确认；未决根因独立保留，恢复不能清空问题。
        state['service_recovered'] = bool(state.get('incident_id') and not state['sensor'].get('active'))
        pending = {q['id']:q for q in state.get('open_questions',[])}
        for q in decision.get('open_questions',[]): pending[q['id']] = copy.deepcopy(q)
        state['open_questions'] = list(pending.values())
        record = dict(service_recovered=state['service_recovered'],open_questions=copy.deepcopy(state['open_questions']),ref=ref, logical_time=tick, event=event, assessment=decision,
                      response_text=turn['response_text'], observations=audit,
                      state_after=state['state'], transition_denied=denied,
                      stop_reason=turn['stop_reason'])
        state['records'].append(record)
        turn['state_record'] = record
        turn['tool_observations'] = [{'source':'harness_state_machine',
            'content':json.dumps(dict(event=event,state_after=state['state'],
                transition_denied=denied,record_ref=ref),ensure_ascii=False)}]
        session['turns'].append(turn)
    session['num_turns'] = len(session['turns'])
    session['incident_id'] = state['incident_id']
    if end_shift:
        transition(state, 'SHIFT_ENDED', '值守轮数结束，保留未决事项供交班复盘',
                   f'{session_id}/end', events_path, state['records'][-1]['logical_time'])
    session['state_after'] = state['state']
    session['transitions'] = [r for r in state['transitions'] if r['record_ref'].startswith(session_id+'/')]
    session['review_input'] = build_review_input([session], state)
    return session, state


def build_review_input(sessions, state):
    return dict(run_id=state['run_id'], shift_id=state['shift_id'],
                session_ids=[s['session_id'] for s in sessions], time_mode='模拟推进的逻辑分钟',
                observations_and_investigation=state['records'], transitions=state['transitions'],
                final_state=state['state'], service_recovered=bool(state.get('service_recovered')),
                open_questions=copy.deepcopy(state.get('open_questions',[])), unresolved=(
                    [state['incident_id']] if state['sensor'].get('active') else []),
                complete=bool(sessions) and all(s['expected_turns'] > 0 and s['num_turns']==s['expected_turns'] and not s.get('budget_exhausted') for s in sessions) and all(t['stop_reason']=='final_answer' and not t.get('tool_errors') and t.get('assessment')
                             and t['observation_reads'] for s in sessions for t in s['turns']))
