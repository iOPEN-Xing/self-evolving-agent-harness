"""复用 examples/assembly 的轻量总装；真实 Hermes，全程本地隔离目录。"""
from common import *
import copy,shutil,signal
sys.path[:0]=[str(HERMES),str(SKILLCLAW),str(ROOT/'examples/assembly')]

from assembly import config
config.MODEL=MODEL;config.BASE_URL=BASE
config.HERMES_SRC=HERMES;config.SKILLCLAW_SRC=SKILLCLAW
# 显式覆盖取钥入口，既不读文件也不接受其他变量。
config.api_key=lambda: os.environ['DEEPSEEK_API_KEY']
import run_assembly as original
from assembly.contracts import tree_hashes,sha256_text,write_json
from payment_grader import install
install()
THIS=Path(__file__).resolve()
ACTIVE_CHILDREN=[]
def stop_children(_signum=None,_frame=None):
 for child in list(ACTIVE_CHILDREN):
  if child.poll() is None:
   try:os.killpg(child.pid,signal.SIGTERM)
   except ProcessLookupError:pass
   try:child.wait(timeout=2)
   except subprocess.TimeoutExpired:
    try:os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    child.wait()
 if _signum is not None:raise SystemExit(128+_signum)
signal.signal(signal.SIGTERM,stop_children)


def subprocess_bounded(args,log,seconds):
 remaining=float(os.environ.get('ASSEMBLY_DEADLINE',str(time.time()+seconds)))-time.time()
 if remaining<=0:raise TimeoutError('整轮预算耗尽，停止启动下一步')
 started=time.time()
 with Path(log).open('w') as stream:
  proc=subprocess.Popen([str(PYTHON),'-B',str(THIS),*args],stdout=stream,stderr=subprocess.STDOUT,env=os.environ.copy(),start_new_session=True)
  ACTIVE_CHILDREN.append(proc)
  try:proc.wait(timeout=min(seconds,remaining))
  except subprocess.TimeoutExpired:
   os.killpg(proc.pid,signal.SIGTERM)
   try:proc.wait(timeout=3)
   except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
   write(Path(log).with_suffix('.process.json'),{'timed_out':True,'returncode':proc.returncode,'elapsed':time.time()-started})
   raise
 ACTIVE_CHILDREN.remove(proc)
 write(Path(log).with_suffix('.process.json'),{'timed_out':False,'returncode':proc.returncode,'elapsed':time.time()-started})
 if proc.returncode:raise RuntimeError('子进程未完成；原始日志已保留')

def worker(request_file):
 req=read(request_file);e=Path(req['evidence']);e.mkdir(parents=True,exist_ok=True)
 config.OUTPUT_DIR=Path(req['home']).parents[1]
 from openai.resources.chat.completions import Completions
 real=Completions.create;calls=[]
 def recorded(self,*a,**kw):
  response=real(self,*a,**kw)
  record={'model':getattr(response,'model',None),'id':getattr(response,'id',None),'usage':None,'requested_model':kw.get('model'),'max_tokens':kw.get('max_tokens'),'max_completion_tokens':kw.get('max_completion_tokens'),'stream':bool(kw.get('stream'))}
  calls.append(record)
  if not kw.get('stream'):
   record['usage']=response.usage.model_dump() if response.usage else None
   write(e/'model_calls.json',calls);return response
  class ObservedStream:
   def __getattr__(self,name):return getattr(response,name)
   def __enter__(self):response.__enter__();return self
   def __exit__(self,*args):return response.__exit__(*args)
   def __iter__(self):
    try:
     for chunk in response:
      if getattr(chunk,'model',None):record['model']=chunk.model
      if getattr(chunk,'id',None):record['id']=chunk.id
      if getattr(chunk,'usage',None):record['usage']=chunk.usage.model_dump()
      yield chunk
    finally:write(e/'model_calls.json',calls)
  return ObservedStream()
 Completions.create=recorded
 try:
  if req.get('kind')!='eval':
   # 只将本练习拥有的隔离基线交给原生维护；不涉及个人技能。
   from assembly.runtime import foreground
   original_make=foreground.make_agent
   def owned_make(instance,home):
    agent=original_make(instance,home)
    from tools.skill_usage import adopt_skill,get_record
    ok,message=adopt_skill(config.SKILL_NAME)
    write(e/'ownership.json',{'skill':config.SKILL_NAME,'home':str(home),'adopted':ok,'message':message,'record':get_record(config.SKILL_NAME),'scope':'本练习创建的隔离教学方法；沿用原生维护授权，不关闭归属保护'})
    if not ok:raise RuntimeError('隔离教学Skill未能交给原生维护')
    return agent
   foreground.make_agent=owned_make
   try:return original.worker(request_file)
   finally:foreground.make_agent=original_make
  from assembly.runtime.foreground import make_agent,run_task
  home=Path(req['home']);local=home/'skills'/config.SKILL_NAME
  shutil.copytree(req['adopted'],local)
  agent=make_agent(req['instance'],home)
  # 评分期间禁止后台改变被评方法；业务系统提示保持现有 assembly 原样。
  agent._memory_nudge_interval=0;agent._skill_nudge_interval=0
  agent.request_overrides['temperature']=0
  skill=(local/'SKILL.md').read_text()
  prompt=req['prompt']+'\n\n本轮已加载技能正文：\n'+skill
  write(e/'loaded_context.json',{'content':skill,'sha256':sha(skill),'delivery':'实际 user_message 预载，不宣称 skill_view'})
  try:
   result=run_task(agent,req['instance'],prompt)
   write_json(e/'task.json',result);write(e/'messages.json',agent._session_messages)
   write(e/'persisted_messages.json',agent._runtime_db.get_messages(result.session_id))
   write(e/'completion.json',{'before':tree_hashes(Path(req['adopted'])),'after':tree_hashes(local),'foreground_result':agent._runtime_last_result})
   if result.stop_reason!='final_answer':raise RuntimeError('任务未正常结束')
  finally:agent.close();agent._runtime_db.close()
 finally:Completions.create=real

def hermes_answers(skill_dir,cases):
 from assembly.eval.runner import AnswerBatch,skill_tree_hash
 run_id=uuid.uuid4().hex;e=config.OUTPUT_DIR/'hermes-eval'/run_id;e.mkdir(parents=True)
 frozen=e/'skill';shutil.copytree(skill_dir,frozen)
 original_hash=skill_tree_hash(skill_dir);data=(config.SCENARIOS_DIR/'orders.json').read_bytes()
 write(e/'cases.json',cases);(e/'orders.json').write_bytes(data)
 context={'run_id':run_id,'run_started_at':time.time(),'model':MODEL,'base_url':BASE,'model_parameters':{'temperature':0,'max_tokens':4096,'max_iterations':12,'thinking':'disabled'},'data_hash':sha(data),'case_set_hash':sha(json.dumps(cases,ensure_ascii=False,sort_keys=True)),'execution_protocol':sha(THIS.read_bytes()),'skill_hash':original_hash,'skill_manifest':tree_hashes(frozen),'isolated':True,'isolation':'每题独立Hermes进程和home；原assembly业务提示；关闭后台修改；真实支付查询工具','cases':[{'case_id':c['case_id'],'prompt_hash':sha(c['prompt'])} for c in cases],'case_specs':cases,'answer_hashes':{},'run_directory':str(e),'trace_files':[]}
 rows=[];models=[];grounded=True
 for i,c in enumerate(cases):
  d=e/c['case_id'];d.mkdir();instance='eval-'+run_id[:8]+'-'+str(i);home=config.OUTPUT_DIR/'homes'/instance
  req={'kind':'eval','instance':instance,'home':str(home),'evidence':str(d),'adopted':str(frozen),'prompt':c['prompt'],'order_id':c['case_id']}
  write(d/'request.json',req);subprocess_bounded(['--worker',str(d/'request.json')],d/'run.log',150)
  task=read(d/'task.json');raw=read(d/'model_calls.json');models.extend(x['model'] for x in raw)
  target=[t for t in task['tool_calls'] if t['name']=='query_order_payments' and t['arguments']=={'order_id':c['case_id']} and not t['has_error']]
  from assembly.runtime.payment_tool import query_order_payments
  expected=query_order_payments(c['case_id'])
  grounded &= bool(target) and all(json.loads(t['result'])==expected for t in target)
  grounded &= read(d/'completion.json')['after']==tree_hashes(frozen)
  rows.append((c,task['answer']));context['answer_hashes'][c['case_id']]=sha(task['answer']);context['trace_files'].append(str(d/'task.json'))
  print('Hermes',Path(skill_dir).name,c['case_id'],'完成',flush=True)
 context.update(tool_grounded=bool(grounded),skill_unchanged=original_hash==skill_tree_hash(skill_dir),data_unchanged=data==(config.SCENARIOS_DIR/'orders.json').read_bytes(),response_models=sorted(set(models)),response_model_confirmed=bool(models) and all(m==MODEL for m in models),run_finished_at=time.time())
 write(e/'context.json',context);write(e/'answers.json',[{'case_id':c['case_id'],'answer':a} for c,a in rows])
 return AnswerBatch(rows,context)

class LightAssembly(original.Assembly):
 def runtime(self,tag,order_id,prompt,dependencies=()):
  d=self.current/tag;d.mkdir(parents=True);instance='asm-'+self.trace['run_id']+'-'+tag
  home=self.root/'homes'/instance;self.trace['homes'].append(str(home));self.save()
  req={'instance':instance,'home':str(home),'evidence':str(d),'adopted':str(self.adopted),'order_id':order_id,'prompt':prompt,'background_timeout':120}
  write(d/'request.json',req);version=self.version(self.adopted)
  subprocess_bounded(['--worker',str(d/'request.json')],d/'run.log',210)
  result=read(d/'result.json');tid='task:'+result['task']['session_id'];bid='background:'+result['task']['session_id']
  self.node(tid,'TaskRun',result['task'],d/'foreground.json',(version,*dependencies))
  self.node(bid,'BackgroundReviewResult',result['background'],d/'background_result.json',(tid,))
  result.update(task_node=tid,background_node=bid);return result
 def shared(self):
  # 复用assembly已接入的SkillHub。候选分享给隔离评测实例，正式组只放采用版。
  from skillclaw.skill_hub import SkillHub
  candidate=Path(self.trace['stages']['candidate']['result']['bundle']['candidate_dir'])
  result={'scope':'单机共享后端、真实独立Hermes实例；不启动演化HTTP或多服务','publication_is_adoption':False,'groups':{}}
  for kind,source in [('adopted',self.adopted),('candidate',candidate)]:
   group='assembly-'+kind;h=SkillHub(backend='local',endpoint='',bucket='',access_key_id='',secret_access_key='',local_root=str(self.current/'store'),group_id=group,user_alias='assembly-source')
   package=self.current/kind/'package'/config.SKILL_NAME;shutil.copytree(source,package)
   pushed=h.push_skills(str(package.parent));download=self.current/kind/'download';download.mkdir()
   pulled=h.pull_skills(str(download),mirror=False);manifest=h._load_remote_manifest()
   dest=download/config.SKILL_NAME;assert tree_hashes(dest)==tree_hashes(source)
   # 此处只复测，不改变正式采用目录；仍使用原业务查询工具。
   from assembly.eval.runner import load_cases
   case=copy.deepcopy(next(c for c in load_cases() if c['case_id']=='ORD-A2'))
   case['prompt']='请实际查询 ORD-A2，核实付款状态并说明处理队列；若没有内部队列约定就明确未知，不猜测。'
   batch=hermes_answers(dest,[case]);answer=batch[0][1]
   assert batch.evaluation_context['tool_grounded'] and batch.evaluation_context['response_model_confirmed'], '共享复测缺少真实回执或模型确认'
   result['groups'][kind]={'push':pushed,'pull':pulled,'manifest':manifest,'source_hashes':tree_hashes(source),'loaded_hashes':tree_hashes(dest),'answer':answer,'context':batch.evaluation_context,'use':'正式采用版' if kind=='adopted' else '隔离复测候选，不用于正式请求'}
  result['different_answer']=result['groups']['adopted']['answer']!=result['groups']['candidate']['answer']
  path=self.current/'sharing.json';write(path,result)
  result['shared_node']=self.node('shared:'+self.trace['run_id'],'SkillClawSharing',result,path,(self.trace['stages']['eval']['result']['decision_node'],))
  return result

def run(lecture):
 rootbase=DELIVERY/'output'/lecture;rootbase.mkdir(parents=True,exist_ok=True)
 original.OUTPUT=rootbase;original.SCRIPT=THIS
 config.HERMES_SRC=HERMES;config.SKILLCLAW_SRC=SKILLCLAW
 run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
 engine=LightAssembly(run_id)
 write(engine.root/'plan.json',{'model':MODEL,'whole_run_seconds':1500,'deadline_epoch':float(os.environ['ASSEMBLY_DEADLINE']),'task_timeout':150,'foreground_background_timeout':210,'background_join_timeout':120,'task_retry':0,'internal_retry':'Hermes保留原生网络重试；整任务不自动重跑','max_iterations':12,'max_tokens':4096,'background_max_iterations':16,'candidate_search_rounds':1,'ownership':'只对隔离教学基线调用原生adopt_skill，允许后台维护；个人技能不涉及','holdout':'同4笔订单的新问法，非独立新订单','sharing':'采用组与候选复测组隔离；本机SkillHub；无完整演化服务','no_gain':'成对主集通过率没有严格提升则保留基线','adoption_rules':['评测记录可信','评分器自测通过','关键用例不退化','候选关键要求全部满足','主组严格增益','同订单的新问法复测不退化'],'new_order_reserve':str(DELIVERY/'new-order-reserve'),'new_order_reserve_used_for_generation_or_selection':False})
 from assembly.eval import runner,policy
 runner.run_answers=hermes_answers
 native_decide=policy.decide
 def measured_decide(candidate,baseline,*args,**kwargs):
  decision=native_decide(candidate,baseline,*args,**kwargs)
  gain=candidate.pass_rate-baseline.pass_rate
  decision.checks.append({'name':'integration.measurable_gain','passed':gain>0,'gain':gain,'source':'本轮总装新增条件；原policy只要求不劣'})
  if gain<=0 or any(not c['passed'] for c in decision.checks):
   decision.decision='REJECT';decision.new_skill_hash=decision.previous_skill_hash;decision.reason='采用条件未全部满足，保留基线；主组须严格增益，其余失败原因见 checks。'
  return decision
 policy.decide=measured_decide
 error=None
 try:
  prepare()
  from assembly.eval.judge import judge_self_check
  check=judge_self_check();write(engine.root/'grader-selftest.json',check.to_dict())
  if not check.passed:raise RuntimeError('评分器自测未通过；停止本轮，保持正式基线')
  for stage in original.STAGES:engine.execute(stage)
 except BaseException as exc:
  error={'type':type(exc).__name__,'message':str(exc)}
  write(engine.root/'error.json',error)
  raise
 finally:
  engine.trace['protected_unchanged']=engine.trace['protected_before']==original.protected_files()
  engine.trace['lightweight_scope']='前台/后台/候选/评分/快照沿用assembly；Hermes评测和SkillHub轻量共享为本轮适配'
  engine.save()
  finished=[s for s in original.STAGES if engine.trace['stages'].get(s,{}).get('status')=='passed']
  write(engine.root/'stage-status.json',{'last_completed_stage':finished[-1] if finished else None,'stages':engine.trace['stages'],'error_file':'error.json' if error else None,'formal_method_hashes':tree_hashes(engine.adopted),'repair_policy':'修复后另建运行编号，不覆盖旧轮'})
  write(engine.root/'completion.json',{'all_stages_finished':all(engine.trace['stages'].get(s,{}).get('status')=='passed' for s in original.STAGES),'protected_unchanged':engine.trace['protected_unchanged'],'run_root':str(engine.root)})
 print('总装结果',engine.root,flush=True)

if __name__=='__main__':
 if len(sys.argv)<2:raise SystemExit('用法：run_assembly_light.py verify 或 capstone')
 if sys.argv[1]=='verify':
  from payment_grader import verify
  verify()
 else:
  if sys.argv[1]=='--worker':
   prepare();worker(sys.argv[2])
  elif sys.argv[1]=='--inside':run(sys.argv[2])
  elif sys.argv[1]=='capstone':
   lecture=sys.argv[1];base=DELIVERY/'output'/lecture;base.mkdir(parents=True,exist_ok=True)
   os.environ['ASSEMBLY_DEADLINE']=str(time.time()+1500)
   subprocess_bounded(['--inside',lecture],base/('run-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.log'),1500)
  else:raise SystemExit('主练习参数为 capstone；巡检版本另见 capstone.py')
