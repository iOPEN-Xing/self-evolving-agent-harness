from common import *
prepare()
p=Path(sys.argv[1]);cfg=read(p);d=p.parent;home=d/'home';home.mkdir()
os.environ['HERMES_HOME']=str(home);os.environ['XDG_CACHE_HOME']=str(home/'cache')
(home/'config.yaml').write_text('tools:\n  tool_search:\n    enabled: false\nmemory:\n  memory_enabled: false\n  user_profile_enabled: false\nskills:\n  creation_nudge_interval: 0\nagent:\n  environment_probe: false\nsessions:\n  write_json_snapshots: false\ncurator:\n  enabled: false\n')
from run_agent import AIAgent
from hermes_state import SessionDB
from tools.registry import registry
import shutil
skill_dest=home/'skills/service-diagnosis/SKILL.md';skill_dest.parent.mkdir(parents=True);shutil.copy2(cfg['skill'],skill_dest)
skill=Path(cfg['skill']).read_text();write(d/'loaded_context.json',{'sha256':sha(skill),'content':skill,'delivery':'磁盘版本；实际读取须检查 messages 中 skill_view 的工具回执'})
snapshot={'health':{'healthy':True},'provider':{'latency_ms':1300 if cfg['case'].startswith('upstream') else 120},'pool':{'capacity':80,'active':80,'waiting':3,'max_wait_seconds':120}}
write(d/'snapshot.json',snapshot)
executions=[]
def execute(args,**ctx):
 command=args['command'];allowed={f'python inspect_snapshot.py {x}':x for x in ('health','provider','pool')}
 if command not in allowed:return json.dumps({'error':'仅允许本地快照查询命令'},ensure_ascii=False)
 started=time.time();proc=subprocess.run([sys.executable,str(DELIVERY/'inspect_snapshot.py'),str(d/'snapshot.json'),allowed[command]],capture_output=True,text=True,timeout=10)
 rec={'command':command,'exit_code':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,'elapsed_sec':time.time()-started}
 executions.append(rec);write(d/'executions.json',executions)
 return json.dumps(rec,ensure_ascii=False)
registry.register(name='run_shell',toolset='lecture_diagnostics',schema={'name':'run_shell','description':'执行只读快照检查命令；可用形式为 python inspect_snapshot.py health、provider 或 pool。','parameters':{'type':'object','properties':{'command':{'type':'string'}},'required':['command'],'additionalProperties':False}},handler=execute,description='只读诊断命令')
db=SessionDB(home/'state.db')
overrides={'extra_body':{'thinking':{'type':'disabled'}},'temperature':0}
if cfg['base']!=BASE:overrides['extra_headers']={'X-Session-Id':cfg['session'],'X-Turn-Type':'main'}
agent=AIAgent(model=MODEL,provider='glm',api_key=os.environ['GLM_API_KEY'],base_url=cfg['base'],api_mode='chat_completions',max_iterations=6,max_tokens=2200,enabled_toolsets=['lecture_diagnostics','skills'],quiet_mode=True,skip_memory=True,skip_context_files=True,save_trajectories=False,session_id=cfg['session'],session_db=db,request_overrides=overrides,ephemeral_system_prompt='你是诊断助手。真实调用工具，只根据回执下结论。遵循本轮加载的方法；不能把建议写成已执行。最后用中文说明已查明的原因和未确认事项。')
prompt=cfg.get('prompt','请求持续超时。先调用 skill_view 读取 service-diagnosis，再严格执行其中的诊断命令并给出结论。不要修改技能。')
started=time.time()
try:
 result=agent.run_conversation(user_message=prompt)
 write(d/'first-result.json',{k:result.get(k) for k in ['final_response','completed','api_calls']})
 if cfg.get('feedback'):
  write(d/'first-messages.json',agent._session_messages)
  result=agent.run_conversation(user_message=cfg['feedback'],conversation_history=agent._session_messages)
 messages=agent._session_messages
 write(d/'messages.json',messages)
 view_ids={c['id'] for m in messages for c in m.get('tool_calls',[]) if c.get('function',{}).get('name')=='skill_view'}
 view_results=[m.get('content','') for m in messages if m.get('role')=='tool' and m.get('tool_call_id') in view_ids]
 def loaded(v):
  try: value=json.loads(v)
  except (ValueError,TypeError): return False
  return isinstance(value,dict) and value.get('success') is True and skill.strip() in str(value.get('raw_content',value.get('content','')))
 assert any(loaded(v) for v in view_results), '未核实原生skill_view实际读取正文'
 write(d/'task-version.json',{'demo_label':cfg.get('demo_label','source-A'),'shared_version':cfg.get('shared_version'),'shared_sha256':cfg.get('shared_sha256'),'task_content_sha256':sha(skill),'native_skill_view_verified':True})
 task={'session_id':cfg['session'],'model':MODEL,'case':cfg['case'],'base':cfg['base'],'skill_sha256':sha(skill),'answer':result.get('final_response'),'completed':result.get('completed'),'failed':result.get('failed'),'api_calls':result.get('api_calls'),'input_tokens':result.get('input_tokens'),'output_tokens':result.get('output_tokens'),'elapsed_sec':time.time()-started,'commands':executions}
 write(d/'task.json',task)
 write(d/'persisted_messages.json',db.get_messages(cfg['session']))
 if not task['completed'] or task['failed']:raise RuntimeError('Hermes未正常完成')
finally:agent.close();db.close()
