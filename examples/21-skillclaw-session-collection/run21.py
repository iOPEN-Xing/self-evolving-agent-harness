from common import *
import socket,asyncio
prepare()
from skillclaw.config import SkillClawConfig
from skillclaw.api_server import SkillClawAPIServer
from skillclaw.skill_hub import SkillHub
from candidate_evidence import materialize_candidates
root=new_run('lecture21');skill=root/'formal/service-diagnosis/SKILL.md';skill.parent.mkdir(parents=True);skill.write_text(V1)
hub=SkillHub(backend='local',endpoint='',bucket='',access_key_id='',secret_access_key='',local_root=str(root/'offline/store'),group_id='lecture21',user_alias='collector')
hub.push_skills(str(root/'formal'))
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
cfg=SkillClawConfig(proxy_host='127.0.0.1',proxy_port=port,claw_type='hermes',configure_openclaw=False,llm_api_base=BASE,llm_api_key=os.environ['DEEPSEEK_API_KEY'],llm_model_id=MODEL,served_model_name=MODEL,record_dir=str(root/'proxy'),record_enabled=True,use_prm=False,use_skills=False,sharing_enabled=True,sharing_backend='local',sharing_local_root=str(root/'offline/store'),sharing_group_id='lecture21',sharing_user_alias='collector',skills_dir=str(root/'proxy-skills'),validation_enabled=False,dashboard_enabled=False,max_context_tokens=64000)
proxy=SkillClawAPIServer(cfg)
@proxy.app.post('/collect/close/{sid}')
async def close_session(sid:str):
 await proxy._close_session(sid,reason='exercise_finished')
 tasks=list(proxy._background_tasks)
 if tasks: await asyncio.gather(*tasks)
 return {'closed':sid}
proxy.start()
if not proxy.wait_until_ready(30):raise RuntimeError('代理未启动')
import httpx
results=[]
def records():return [json.loads(x) for x in (root/'proxy/conversations.jsonl').read_text().splitlines() if x]
try:
 for i,case in enumerate(('upstream-1','upstream-2','upstream-3','pool-1')):
  task=run_worker(root,'task-'+str(i+1),case,skill,f'http://127.0.0.1:{port}/v1',worker='learning_worker.py');results.append(task)
  with httpx.Client(trust_env=False) as c:c.post(f'http://127.0.0.1:{port}/collect/close/'+task['session_id']).raise_for_status()
  write(root/'progress.json',results);print('已完成',case,flush=True)
 before=records();direct=run_worker(root,'direct','pool-direct',skill,worker='learning_worker.py');after=records()
 write(root/'bypass.json',{'before_records':len(before),'after_records':len(after),'before_sessions':len({x['session_id'] for x in before}),'after_sessions':len({x['session_id'] for x in after}),'direct_session':direct['session_id'],'direct_completed':direct['completed'],'direct_absent':not any(x['session_id']==direct['session_id'] for x in after)})
 # 原始轮次与修订后指标各自保留；严格解析回执的审计器负责指标。
 subprocess.run([sys.executable,str(DELIVERY/'audit21.py'),str(root)],check=True)
 audit=read(root/'signal-audit.json')
 with (root/'offline/run.log').open('w') as log:
  subprocess.run([str(PYTHON),'-B',str(DELIVERY/'evolve_once.py'),str(root/'offline'),'validated'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
 candidates=materialize_candidates(root/'offline',V1)
 assert sha(skill.read_bytes())==sha(V1), '正式方法被改变'
 summary={'candidates':candidates,'formal_sha256':sha(skill.read_bytes()),'root':str(root),'model':MODEL,'proxy_sessions':len({x['session_id'] for x in after}),'proxy_records':len(after),'signal_observed':audit['signal_observed'],'total':audit['total'],'bypass':read(root/'bypass.json'),'skill_unchanged':sha(skill.read_text())==sha(V1),'scope':'原生代理采集与单次原生离线管线；候选在隔离区，未业务评测或采用；统计以本轮signal-audit为准'}
 write(root/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
finally:proxy.stop()
