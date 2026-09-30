"""离线边界测试；夹具不计入联网结果。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shift.runtime import read_observation, poll_sensor, ObservationTools, transition, run_shift
from eval.judge import parse_judge_response, self_check, deterministic_score
from eval.cases import EVAL_CASES
from eval.runner import run_eval, summarize, compare_versions
from versioning.policy import decide


class Boundaries(unittest.TestCase):
    def test_current_snapshot_only(self):
        o=read_observation('training',0)
        self.assertNotIn('snapshots',o)
        self.assertNotIn('effective20',json.dumps(o))
        tools=ObservationTools('training',0,[])
        for args in [dict(service='search-api',logical_time=5),dict(service='other',logical_time=0)]:
            with self.assertRaises(ValueError):tools('read_config',args)

    def test_sensor_no_short_recovery(self):
        memory={}
        events=[poll_sensor(read_observation('training',t),memory)['type'] for t in [0,5,10,15,20,25]]
        self.assertEqual(events,['NORMAL','ALERT','ALERT_ONGOING','RECOVERY_PENDING','RECOVERY_PENDING','RECOVERY'])

    def test_sensor_stale_missing(self):
        for t in [5,10]:
            self.assertEqual(poll_sensor(read_observation('RG-01',t),{})['type'],'OBSERVATION_UNKNOWN')

    def test_single_instance_alarm(self):
        self.assertEqual(poll_sensor(read_observation('NI-02',5),{})['type'],'ALERT')

    def test_illegal_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                transition({'state':'PATROLLING'},'VERIFYING','bad','x',Path(tmp)/'t.jsonl',0)

    def test_strict_parser(self):
        for raw in ['', 'score: 5','```json\n{"score":5,"reason":"ok"}\n```',
                    '{"score":true,"reason":"ok"}','{"score":6,"reason":"ok"}',
                    '{"score":5,"score":1,"reason":"ok"}','{"score":5,"reason":"ok","x":1}']:
            self.assertEqual(parse_judge_response(raw)['status'],'parse_error')
        self.assertEqual(parse_judge_response('{"score":5,"reason":"正确"}')['score'],5)

    def test_bad_self_check_rejects(self):
        sc=self_check(lambda *a,**k:'{"score":5,"reason":"错误样本也放行"}')
        self.assertFalse(sc['passed'])
        self.assertEqual(decide({},self_check=sc)['decision'],'REJECT')

    def test_parse_error_rejects(self):
        sc=self_check(lambda *a,**k:'not json')
        self.assertFalse(sc['passed'])
        self.assertEqual(decide({},self_check=sc)['decision'],'REJECT')

    def test_self_check_fixtures(self):
        responses=iter(['{"score":1,"reason":"结案与观测矛盾"}','{"score":5,"reason":"有依据且谨慎"}'])
        sc=self_check(lambda *a,**k:next(responses))
        self.assertTrue(sc['passed'])
        self.assertTrue(sc['deterministic_only_pass'])

    def test_missing_run_case(self):
        with self.assertRaises(ValueError):run_eval('skill','v0',lambda *a,**k:'unused')

    def fake_turn(self,prompt,ref,dispatch,context,messages):
        result=dispatch('read_metrics',dict(service=context['service'],logical_time=context['logical_time'],scope='overall'))
        # 故意在短暂回落时申请恢复；状态机必须拒绝。
        target='VERIFYING' if context['state']=='INVESTIGATING' else 'PATROLLING'
        return dict(messages=[],response_text='离线测试夹具',tool_calls=[{}],tool_errors=[],
                    stop_reason='final_answer',assessment={'assessment':'recovered','next_state':target})

    def test_handoff_and_recovery_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=dict(scenario_id='training',run_turn=self.fake_turn,run_id='test',shift_id='s',events_path=Path(tmp)/'t.jsonl')
            first,state=run_shift(**args,ticks=[5],session_id='s1')
            with self.assertRaises(ValueError):run_shift(**args,ticks=[15],session_id='s2',state=state)
            second,state=run_shift(**args,ticks=[15],session_id='s2',state=state,investigation_record=state['records'])
            self.assertEqual(state['state'],'VERIFYING')
            self.assertIsNotNone(second['turns'][0]['state_record']['transition_denied'])
            self.assertEqual(second['received_investigation'],first['review_input']['observations_and_investigation'])

    def test_budget_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            session,state=run_shift(scenario_id='training',ticks=[0,5],run_turn=self.fake_turn,
                run_id='test',shift_id='s',session_id='s1',events_path=Path(tmp)/'t.jsonl',max_patrol_rounds=1)
            self.assertTrue(session['budget_exhausted'])
            self.assertFalse(session['review_input']['complete'])

    def test_missing_scores_separate_from_valid_failure(self):
        def row(cid,status,passed,score):
            return dict(case_id=cid,set='regression',llm_judge_status=status,
                        passed=passed,llm_judge_score=score)
        v0={'results':[row('NI-01','parse_error',False,None),row('HO-02','parse_error',False,None),row('RG-01','ok',False,1)]}
        v1={'results':[row('RG-01','parse_error',False,None),row('HO-02','ok',True,5),row('NI-01','ok',True,5)]}
        summary=summarize(v0)['overall']
        self.assertEqual((summary['missing_scores'],summary['valid_failed']),(2,1))
        result=compare_versions(v0,v1)
        self.assertEqual(len(result['missing_score_items']),3)
        self.assertEqual(result['per_case'][0]['case_id'],'NI-01')
        self.assertEqual(result['per_case'][0]['v1_status'],'passed')
        self.assertFalse(result['ability_improvement_established'])

    def test_recovery_preserves_open_questions(self):
        question=dict(id='Q1',question='漂移原因尚未确认',evidence_refs=['test/t1'],next_check='核对变更记录')
        def turn(*args):
            value=self.fake_turn(*args)
            if args[3]['logical_time']==5:
                value['assessment']['open_questions']=[question]
            return value
        with tempfile.TemporaryDirectory() as tmp:
            session,state=run_shift(scenario_id='training',ticks=[5,10,15,20,25],run_turn=turn,
                run_id='test',shift_id='s',session_id='s1',events_path=Path(tmp)/'t.jsonl')
            self.assertTrue(state['service_recovered'])
            self.assertEqual(state['open_questions'],[question])
            self.assertTrue(session['review_input']['service_recovered'])
            self.assertEqual(session['review_input']['open_questions'],[question])

    def test_no_trace_no_pass(self):
        checks=deterministic_score('声称全部查过',EVAL_CASES[0]['assertions'])
        self.assertFalse(all(x[0] for x in checks.values()))

if __name__=='__main__':unittest.main(verbosity=2)
