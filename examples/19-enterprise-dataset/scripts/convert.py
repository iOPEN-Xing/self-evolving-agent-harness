#!/usr/bin/env python3
"""固定版本教学导出 → 非交互筛选 → 原生 YAML；不参与运行和发布。"""
import json,hashlib,shutil,sys
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,x): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def main():
    export=json.loads((ROOT/'source/langfuse-export.teaching.json').read_text()); rejected=[]; accepted=[]; seen=set()
    for item in export['items']:
        m=item['metadata']; snap=ROOT/'source/snapshots'/item['input']['snapshot']; reasons=[]
        if not m.get('initial_request_complete') or m.get('user_followups') or m.get('requires_user_choice'): reasons.append('存在交互依赖')
        if not snap.is_dir(): reasons.append('缺任务起点快照')
        if not m.get('ground_mature') or m.get('ground_disputed') or not item.get('expectedOutput'): reasons.append('独立标准未成熟或有争议')
        if not m.get('task_finished'): reasons.append('不能确定任务结束')
        if not m.get('truth_source','').startswith('teaching-ledger-facts:'): reasons.append('缺独立账务依据')
        if not reasons:
            for name,sha in m['snapshot_hashes'].items():
                if digest(snap/(name+'.json'))!=sha: reasons.append('快照哈希不符')
        group=(m['request_id'],m['truth_valid_at'])
        if not reasons and group in seen: reasons.append('同一业务事件重复')
        if reasons:
            rejected.append(dict(id=item['id'],reasons=reasons,destination='交互专项评测' if '存在交互依赖' in reasons else '候选区：补快照或独立标准后重新确定数据版本')); continue
        seen.add(group); cid=item['id']; fixture=ROOT/'evals/fixtures/inputs'/cid/'input'; fixture.mkdir(parents=True,exist_ok=True)
        for p in snap.glob('*.json'): shutil.copy2(p,fixture/p.name)
        oracle=ROOT/'evals/grader-only'/f'{cid}.json'; dump(oracle,item['expectedOutput'])
        # 评分程序在服务结束后由 skill-up 载入。单文件内嵌标准，避免依赖未来事件。
        script=ROOT/'evals/grader-only'/f'{cid}.py'; script.write_text((ROOT/'scripts/grader.py').read_text()+'\nORACLE='+repr(item['expectedOutput'])+'\nif __name__ == "__main__":\n    result=grade(os.environ.get("EVAL_FINAL_MESSAGE",""),ORACLE,int(os.environ.get("EVAL_EXIT_CODE","1")))\n    print(json.dumps(result,ensure_ascii=False))\n    raise SystemExit(0 if result["passed"] else 1)\n'); script.chmod(0o755)
        case=dict(id=cid,title=m['stratum'],input=dict(prompt=item['input']['task']),context=dict(repo_fixture=f'evals/fixtures/inputs/{cid}'),constraints=dict(timeout_seconds=300),expect=dict(exit_code=0),judge=dict(type='script',script_path=f'evals/grader-only/{cid}.py',timeout_seconds=30))
        dest=ROOT/'evals/cases'/f'{cid}.yaml';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(yaml.safe_dump(case,allow_unicode=True,sort_keys=False))
        accepted.append(dict(case_id=cid,source_item_id=cid,source_trace_id=item['sourceTraceId'],source_observation_id=item['sourceObservationId'],session_id=m['session_id'],request_id=m['request_id'],stratum=m['stratum'],split=m['split'],truth_source=m['truth_source'],truth_valid_at=m['truth_valid_at'],snapshot_hashes=m['snapshot_hashes'],historical_feedback=m['historical_feedback']))
    for variant in ['legacy','hermes']:
        config=dict(schema_version='v1alpha1',environment=dict(type='none'),skills=[dict(source='local_path',path='skill/payment-report',target='skills/payment-report',include=['SKILL.md'])],engine=dict(name=f'course-{variant}',custom=dict(transport='local',conversation_mode='batch',response_format='session_result',timeout_seconds=300,local=dict(command='python3',args=['scripts/engine.py','--variant',variant,'--input','${input_file}','--output','${output_file}'],output_file='engine/session-result.json'))),cases=dict(files=[f'evals/cases/{a["case_id"]}.yaml' for a in accepted],parallelism=1,retry_policy=dict(max_retries=0)),benchmark=dict(enabled=False),report=dict(formats=['json'],artifacts=['transcript']))
        (ROOT/'evals'/f'eval.{variant}.yaml').write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    for variant in ['legacy','hermes']:
        config=yaml.safe_load((ROOT/'evals'/f'eval.{variant}.yaml').read_text())
        config['engine']['custom']['timeout_seconds']=630
        config['cases']['files']=[f'evals/cases/payment-{n:03d}.yaml' for n in [1,2,4,10]]
        (ROOT/'evals'/f'eval.demo.{variant}.yaml').write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    manifest=dict(schema='course-manifest-v1',dataset_name=export['dataset_name'],dataset_version=export['dataset_version'],provenance=export['provenance'],converter_sha256=digest(Path(__file__)),grader_sha256=digest(ROOT/'scripts/grader.py'),skill_sha256=digest(ROOT/'skill/payment-report/SKILL.md'),independent_cases=len(accepted),cases=accepted)
    dump(ROOT/'manifest.json',manifest)
    (ROOT/'rejected.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rejected))
    print(f'原生 YAML 已生成：收入 {len(accepted)}，排除 {len(rejected)}。')
if __name__=='__main__': main()
