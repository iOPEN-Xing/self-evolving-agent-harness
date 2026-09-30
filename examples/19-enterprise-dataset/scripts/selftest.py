#!/usr/bin/env python3
"""检查评分是否真正依赖本次答案，以及工具能否越界读取评分材料。"""
import os,json,copy,sys,subprocess,shutil
from isolation import launch
from pathlib import Path
from grader import grade
from readonly_tools import read_table
ROOT=Path(__file__).resolve().parents[1];OUT=Path(os.environ.get('LECTURE19_OUTPUT_DIR',str(ROOT/'output/demo'))).resolve();OUT.mkdir(parents=True,exist_ok=True)
def main():
    oracle=json.loads((ROOT/'evals/grader-only/payment-009.json').read_text()); tests=[]
    def check(name,obj,want):
        result=grade(json.dumps(obj,ensure_ascii=False),oracle);tests.append(dict(name=name,expected_pass=want,actual_pass=result['passed'],check_passed=result['passed']==want,errors=result['errors']))
    check('正确答案',oracle,True)
    obj=copy.deepcopy(oracle);obj['orders'][0]['status']='已到账';obj['orders'][0]['explanation']='表达不同不影响事实。';check('正确同义结果',obj,True)
    obj=copy.deepcopy(oracle);obj['orders'][0]['status']='unpaid';check('状态反转',obj,False)
    obj=copy.deepcopy(oracle);obj['orders'][0]['merchant_id']='other';check('跨商户串单',obj,False)
    obj=copy.deepcopy(oracle);obj['orders'].pop();check('漏单',obj,False)
    obj=copy.deepcopy(oracle);obj['orders'][0]['effective_paid_cents']+=1;check('金额错误',obj,False)
    obj=copy.deepcopy(oracle);obj['orders'][0]['evidence_ids']=['not-in-snapshot'];check('依据不符',obj,False)
    check('忽略输出只返回历史采纳',{'accepted':1},False)
    for name,text in [('JSON前加分析', '分析说明\n'+json.dumps(oracle)), ('Markdown代码围栏', '```json\n'+json.dumps(oracle)+'\n```')]:
        result=grade(text,oracle);tests.append(dict(name=name,expected_pass=False,actual_pass=result['passed'],check_passed=not result['passed'],errors=result['errors']))
    (OUT/'grader-selftest.json').write_text(json.dumps(dict(passed=all(t['check_passed'] for t in tests),tests=tests),ensure_ascii=False,indent=2))
    workspace=OUT/'runtime/probe-workspace';shutil.copytree(ROOT/'evals/fixtures/inputs/payment-001',workspace,dirs_exist_ok=True)
    payload=dict(workspace=str(workspace),messages=[dict(role='user',content='读取支付核查Skill和快照。')]);payload_path=workspace/'probe-input.json';payload_path.write_text(json.dumps(payload))
    result,runtime=launch([sys.executable,str(ROOT/'scripts/hermes_worker.py'),str(payload_path),str(workspace/'tool-isolation.json'),'--probe'],workspace,sys.executable,output_path=workspace/'tool-isolation.json')
    if result.returncode: raise RuntimeError('隔离运行器失败：'+result.stderr.decode()[:200])
    isolation=json.loads((workspace/'tool-isolation.json').read_text()); assert isolation.get('skill_loaded'),isolation
    assert isolation['blocked_paths']==4,isolation
    # 直接从被测进程调用 open，确认不是只靠工具层拦截。
    paths=[ROOT/'evals/grader-only/payment-001.json',ROOT/'source/langfuse-export.teaching.json',ROOT/'manifest.json',ROOT/'scripts/make_teaching_export.py',ROOT/'evals/fixtures/inputs/payment-002/input/request.json']
    code="import json; from pathlib import Path\nresults=[]\nfor name in "+repr([str(p) for p in paths])+":\n try: Path(name).read_text(); results.append(False)\n except PermissionError: results.append(True)\nprint(json.dumps(results))"
    check,_=launch([sys.executable,'-c',code],workspace,sys.executable)
    assert check.returncode==0,(check.returncode,check.stderr.decode())
    blocked=json.loads(check.stdout);assert all(blocked),blocked
    write_code="from pathlib import Path\ntry: Path("+repr(str(workspace/'input/request.json'))+").write_text('tampered'); print('UNEXPECTED')\nexcept PermissionError: print('DENIED')"
    write_check,_=launch([sys.executable,'-c',write_code],workspace,sys.executable)
    assert write_check.stdout.strip()==b'DENIED',write_check.stderr
    isolation.update(input_write_denied=True,os_sandbox_passed=True,os_denied_files=[str(p.relative_to(ROOT)) for p in paths],os_deny_results=blocked)
    (OUT/'tool-isolation.json').write_text(json.dumps(isolation,ensure_ascii=False,indent=2))
    fixture_files=[str(p.relative_to(ROOT/'evals/fixtures')) for p in (ROOT/'evals/fixtures').rglob('*') if p.is_file()]
    assert all(p.endswith(('request.json','orders.json','ledger.json','channel.json')) for p in fixture_files)
    assert all(t['check_passed'] for t in tests)
    print('评分器10项自测通过；真实 Hermes skill_view 本地读取成功，4种越界工具参数被拒绝。未联网调用模型。')
if __name__=='__main__':main()
