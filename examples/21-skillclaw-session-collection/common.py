"""本地练习公共入口；凭据只从显式 GLM_API_KEY 环境变量取得。"""
from pathlib import Path
import os,sys,json,hashlib,subprocess,time,uuid
ROOT=Path(__file__).resolve().parents[2]
DELIVERY=Path(__file__).resolve().parent
HERMES=Path(os.environ.get('HERMES_SRC',str(ROOT/'.deps/hermes-agent'))).resolve()
SKILLCLAW=Path(os.environ.get('SKILLCLAW_SRC',str(ROOT/'.deps/SkillClaw'))).resolve()
PYTHON=HERMES/'.venv/bin/python'
MODEL='glm-5.2'
BASE='https://open.bigmodel.cn/api/paas/v4'
def prepare():
 if not os.environ.get('GLM_API_KEY','').strip(): raise RuntimeError('请先显式加载 GLM_API_KEY')
 for k in ['http_proxy','https_proxy','all_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY']:
  os.environ.pop(k,None)
 os.environ['PYTHONDONTWRITEBYTECODE']='1'
 sys.dont_write_bytecode=True
 os.environ['PYTHON_DOTENV_DISABLED']='1'
 sys.path[:0]=[str(HERMES),str(SKILLCLAW),str(ROOT/'examples/assembly')]
def write(path,obj):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def read(path):return json.loads(Path(path).read_text())
def sha(value):return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()
def new_run(lecture):
 p=DELIVERY/'output'/lecture/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]);p.mkdir(parents=True)
 write(p/'plan.json',{'model':MODEL,'backend':BASE,'lecture':lecture,'max_task_seconds':150,'max_iterations':6,'overall_deadline_enforced':False,'task_retry':0,'records':'教学构造，模型与工具循环真实联网','started_at':time.time()})
 return p
V1='''---
name: service-diagnosis
description: 根据服务健康与上游延迟检查超时原因。
---
# 超时检查
先执行 `python inspect_snapshot.py health` 检查服务健康，再执行 `python inspect_snapshot.py provider` 检查上游延迟。
健康失败时报告服务异常。上游延迟超过1000毫秒时报告上游缓慢。两项都正常而请求仍超时，则报告尚未定位，可以提出后续建议。
只执行本方法列出的检查，不把建议当成已经执行。
'''
def run_worker(root,tag,case,skill,base=BASE,extra=None,worker='hermes_worker.py'):
 d=Path(root)/tag;d.mkdir(parents=True,exist_ok=False)
 write(d/'input.json',{'case':case,'skill':str(Path(skill).resolve()),'base':base,'session':tag+'-'+uuid.uuid4().hex[:8],**(extra or {})})
 with (d/'run.log').open('w') as log:
  p=subprocess.run([str(PYTHON),'-B',str(DELIVERY/worker),str(d/'input.json')],env=os.environ.copy(),stdout=log,stderr=subprocess.STDOUT,timeout=150)
 if p.returncode: raise RuntimeError(f'{tag}运行失败，保留日志：{d}')
 return read(d/'task.json')
