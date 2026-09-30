from common import *
import socket,shutil,asyncio
prepare()
from skillclaw.config import SkillClawConfig
from skillclaw.api_server import SkillClawAPIServer
from skillclaw.skill_hub import SkillHub
import httpx
root=new_run('lecture22');group='lecture22'
seed=root/'seed/service-diagnosis/SKILL.md';seed.parent.mkdir(parents=True);seed.write_text(V1)
def hub(store):return SkillHub(backend='local',endpoint='',bucket='',access_key_id='',secret_access_key='',local_root=str(store),group_id=group,user_alias='instance-A')
direct=root/'direct';direct.mkdir();h=hub(direct/'store');write(root/'seed-push.json',h.push_skills(str(root/'seed')))
with socket.socket() as so:so.bind(('127.0.0.1',0));port=so.getsockname()[1]
cfg=SkillClawConfig(proxy_host='127.0.0.1',proxy_port=port,claw_type='hermes',configure_openclaw=False,llm_api_base=BASE,llm_api_key=os.environ['GLM_API_KEY'],llm_model_id=MODEL,served_model_name=MODEL,record_dir=str(root/'proxy'),record_enabled=True,use_prm=False,use_skills=False,sharing_enabled=True,sharing_backend='local',sharing_local_root=str(direct/'store'),sharing_group_id=group,sharing_user_alias='instance-A',skills_dir=str(root/'proxy-skills'),validation_enabled=False,dashboard_enabled=False,max_context_tokens=64000)
proxy=SkillClawAPIServer(cfg)
@proxy.app.post('/collect/close/{sid}')
async def close_session(sid:str):
 await proxy._close_session(sid,reason='exercise_finished')
 tasks=list(proxy._background_tasks)
 if tasks:await asyncio.gather(*tasks)
 return {'closed':sid}
proxy.start()
if not proxy.wait_until_ready(30):raise RuntimeError('代理未启动')
try:
 feedback='前面的检查仍未定位。本次授权追加只读检查：执行 python inspect_snapshot.py pool，读取连接池容量、活跃数、等待数和最长等待。请据实际回执判断是否存在资源等待，不把一次异常直接宣称为已确认的超时根因；说明这个新增分支的触发条件和后续核查。不要修改本地技能。'
 source=run_worker(root,'source-A','pool-1',seed,f'http://127.0.0.1:{port}/v1',{'feedback':feedback,'demo_label':'source-A-baseline','shared_version':1,'shared_sha256':sha(V1)},worker='learning_worker.py')
 with httpx.Client(trust_env=False) as c:c.post(f'http://127.0.0.1:{port}/collect/close/'+source['session_id'],timeout=30).raise_for_status()
finally:proxy.stop()
sessions=list((direct/'store'/group/'sessions').glob('*.json'));assert len(sessions)==1
write(root/'source-sessions.json',[read(p) for p in sessions]);assert any(t['read_skills'] for t in read(sessions[0])['turns']), '缺少真实Skill读取'
# 两个隔离存储使用同一个真实来源；边界比较不要求模型生成逐字相同候选。
validated=root/'validated';shutil.copytree(direct,validated)
for mode in ('direct','validated'):
 d=root/mode
 write(d/'manifest-before.json',hub(d/'store')._load_remote_manifest())
 with (d/'run.log').open('w') as log:
  proc=subprocess.run([str(PYTHON),'-B',str(DELIVERY/'evolve_once.py'),str(d),mode],stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy(),timeout=240)
 if proc.returncode:raise RuntimeError(mode+'管线失败；保留本轮所有记录')
 write(d/'manifest-after.json',hub(d/'store')._load_remote_manifest());print(mode,'结束',flush=True)
remote=h._load_remote_manifest();assert 'service-diagnosis' in remote
if remote['service-diagnosis']['sha256']==sha(V1):raise RuntimeError('本轮没有产生不同的发布版，不能声称行为改变')
for tag in ('third-loaded','third-unloaded'):
 download=root/tag/'download';download.mkdir(parents=True)
 write(root/(tag+'-pull.json'),h.pull_skills(str(download),mirror=False))
 downloaded=download/'service-diagnosis/SKILL.md';assert sha(downloaded.read_bytes())==remote['service-diagnosis']['sha256']
 # 未加载对照的新版文件确实落盘；实际Hermes技能根仍装旧版。
 selected=downloaded if tag=='third-loaded' else seed
 run_worker(root,tag+'-task','pool-probe',selected,extra={'demo_label':tag,'shared_version':remote['service-diagnosis']['version'],'shared_sha256':remote['service-diagnosis']['sha256']},worker='learning_worker.py')
run_worker(root,'third-v1-task','pool-probe',seed,extra={'demo_label':'old-baseline','shared_version':remote['service-diagnosis']['version'],'shared_sha256':remote['service-diagnosis']['sha256']},worker='learning_worker.py')
anti=run_worker(root,'anti-upstream-task','upstream-anti',downloaded,extra={'demo_label':'healthy-upstream-abnormal','shared_version':remote['service-diagnosis']['version'],'shared_sha256':remote['service-diagnosis']['sha256']},worker='learning_worker.py')
write(root/'anti-branch.json',{'task':anti,'pool_executed':any(c['command']=='python inspect_snapshot.py pool' for c in anti['commands']),'branch_passed':not any(c['command']=='python inspect_snapshot.py pool' for c in anti['commands']),'expectation':'健康正常而上游异常时不应无条件追加连接池检查'})
write(root/'manifest.json',remote)
checks=[]
for tag in ('third-v1-task','third-unloaded-task','third-loaded-task'):
 t=read(root/tag/'task.json');obs=[]
 for c in t['commands']:
  if c['exit_code']==0 and c['command']=='python inspect_snapshot.py pool':
   value=json.loads(c['stdout']);assert value==read(root/tag/'snapshot.json')['pool'];obs.append(value)
 checks.append({'tag':tag,'skill_sha256':t['skill_sha256'],'pool_executed':bool(obs),'pool_receipts':obs,'completed':t['completed'],'answer':t['answer']})
# 旧内容作为一个新版本重新发布；保持版本号递增，不篡改历史版本号。
write(root/'restore-push.json',h.push_skills(str(root/'seed')));restored=h._load_remote_manifest();write(root/'restored-manifest.json',restored)
assert restored['service-diagnosis']['sha256']==sha(V1)
rollback_dir=root/'after-rollback/download';rollback_dir.mkdir(parents=True)
write(root/'rollback-pull.json',h.pull_skills(str(rollback_dir),mirror=False))
rollback_skill=rollback_dir/'service-diagnosis/SKILL.md'
assert sha(rollback_skill.read_bytes())==sha(V1)
rollback=run_worker(root,'after-rollback-task','pool-probe',rollback_skill,extra={'demo_label':'after-rollback','shared_version':restored['service-diagnosis']['version'],'shared_sha256':restored['service-diagnosis']['sha256']},worker='learning_worker.py')
assert rollback['skill_sha256']==sha(V1)
write(root/'rollback-check.json',{'task':rollback,'old_hash_matches':True,'pool_executed':any(c['command']=='python inspect_snapshot.py pool' for c in rollback['commands'])})
write(root/'summary.json',{'root':str(root),'model':MODEL,'source_session':source['session_id'],'direct':read(direct/'evolution.json'),'validated':read(validated/'evolution.json'),'published_manifest':remote,'checks':checks,'restore_manifest':restored,'scope':'原生代理+单进程一次固定管线+原生SkillHub；没有启动完整HTTP或周期服务。真实多进程实例共享本机文件后端，未验证跨主机网络故障。'})
print('结果',str(root/'summary.json'),flush=True)
