#!/usr/bin/env python3
"""预先记录运行条件，执行原生 validate 和两套 engine，不隐藏失败。"""
import json,os,sys,subprocess,hashlib,time
import yaml
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=Path(os.environ.get('LECTURE19_OUTPUT_DIR',str(ROOT/'output/demo'))).resolve()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'protocol.json').exists(): raise SystemExit('输出目录已有一轮记录；请另设 LECTURE19_OUTPUT_DIR，不覆盖历史。')
    # 运行前必须在本轮目录重新验证评分及操作系统隔离，失败即停止。
    subprocess.run([sys.executable,str(ROOT/'scripts/selftest.py')],check=True)
    runtime=OUT/'runtime';runtime.mkdir(exist_ok=True)
    protocol=dict(schema='course-protocol-v1',created_before_runs=True,teaching_only=True,primary_metric='逐题硬字段全部正确的比例',noninferiority_margin=0.01,one_sided_alpha=0.05,repetitions_per_case=1,independent_cases=4,selected_case_ids=['payment-001','payment-002','payment-004','payment-010'],design='四题精简演示；运行器预载Skill及四张快照，每题一次Hermes模型回答；保留逐题对照与本地小流量接线，不承担生产非劣效证明',model_toolsets=[],max_iterations=1,worker_timeout_seconds=600,native_timeout_seconds=630,retry_policy='两侧均不重跑用例；Hermes 保留上游瞬时传输退避，HTTP 状态逐次记录。401 终止本题。',aggregation='先按case配对，再按预定业务类型汇总；同题重复不增加独立样本数',failure_policy='保留全部失败；凭证、超时与业务错误分别报告',hard_errors=['串单','漏单','状态反转','金额错误'],release_policy='教学小样本不决定生产替换；没有完整候选结果时数据不足，保留原服务',model='glm-5.2',response_format={'type':'json_object'},temperature=0,thinking={'type':'disabled'},glm_api_key_present=bool(os.environ.get('GLM_API_KEY')),glm_base_url_present=bool(os.environ.get('GLM_BASE_URL')),langfuse_credentials_present=all(os.environ.get(k) for k in ['LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY','LANGFUSE_BASE_URL']),langfuse_collection_acceptance=False,files_sha256={str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'manifest.json',ROOT/'skill/payment-report/SKILL.md',ROOT/'scripts/legacy_service.py',ROOT/'scripts/hermes_worker.py',ROOT/'scripts/grader.py',ROOT/'scripts/isolation.py',ROOT/'evals/eval.demo.legacy.yaml',ROOT/'evals/eval.demo.hermes.yaml',ROOT/'bin/skill-up']})
    (OUT/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n')
    env=os.environ.copy();env['LECTURE19_OUTPUT_DIR']=str(OUT);env['TMPDIR']=str(runtime);env['PYTHONDONTWRITEBYTECODE']='1';env['PATH']=str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
    # 禁用原生用户配置发现，避免引入本机个人实验配置；不在命令行放密钥。
    user_config=runtime/'skill-up.config.yaml'
    user_config.write_text('{}\n')
    validations=[];runs=[]
    for variant in ['legacy','hermes']:
        source_config=ROOT/'evals'/f'eval.demo.{variant}.yaml'
        # 上游在题目工作区执行引擎；便携配置只在本轮转换为机器上的绝对路径。
        materialized=yaml.safe_load(source_config.read_text())
        local=materialized['engine']['custom']['local']
        local['command']=sys.executable;local['args'][0]=str(ROOT/'scripts/engine.py')
        # 移动配置后显式绑定资源路径，避免相对路径随 YAML 所在目录变化。
        for skill in materialized['skills']:
            if skill['source']=='local_path': skill['path']=str((ROOT/skill['path']).resolve())
        materialized['cases']['files']=[str((ROOT/path).resolve()) for path in materialized['cases']['files']]
        config=OUT/f'eval.runtime.{variant}.yaml'
        rendered=yaml.safe_dump(materialized,allow_unicode=True,sort_keys=False)
        config.write_text(rendered)
        binary=str(ROOT/'bin/skill-up')
        cmd=[binary,'--config',str(user_config),'validate',str(config)];r=subprocess.run(cmd,env=env,cwd=ROOT,capture_output=True,text=True)
        (OUT/f'validate-{variant}.log').write_text(r.stdout+r.stderr);validations.append(dict(variant=variant,returncode=r.returncode))
        if r.returncode: raise SystemExit('原生 YAML 校验失败')
        cmd=[binary,'--config',str(user_config),'run',str(config),'--output-dir',str(OUT/'skill-up'/variant),'--iteration','1','--event-log',str(OUT/f'skill-up-{variant}-events.jsonl')]
        r=subprocess.run(cmd,env=env,cwd=ROOT,capture_output=True,text=True,timeout=4500)
        # 上游在无凭证分支只得到适配器固定信息；凭证绝不置于配置/SessionInput。
        log=r.stdout+r.stderr
        key=os.environ.get('GLM_API_KEY','')
        if key: log=log.replace(key,'[凭证已移除]')
        (OUT/f'run-{variant}.log').write_text(log);runs.append(dict(variant=variant,returncode=r.returncode))
    (OUT/'native-run-status.json').write_text(json.dumps(dict(validations=validations,runs=runs),ensure_ascii=False,indent=2))
    print(json.dumps(dict(validations=validations,runs=runs),ensure_ascii=False))
if __name__=='__main__':main()
