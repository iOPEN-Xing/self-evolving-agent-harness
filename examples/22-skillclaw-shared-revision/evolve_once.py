"""仅在单进程调用原生一次固定管线，不启动HTTP或周期服务。"""
from common import *
import asyncio
prepare()
from evolve_server.core.config import EvolveServerConfig
from evolve_server.engines.workflow import EvolveServer
root=Path(sys.argv[1]);mode=sys.argv[2]
cfg=EvolveServerConfig(storage_backend='local',local_root=str(root/'store'),group_id='lecture22',llm_api_key=os.environ['DEEPSEEK_API_KEY'],llm_base_url=BASE,llm_model=MODEL,llm_max_tokens=12000,llm_temperature=0,use_session_judge=True,use_skill_verifier=False,publish_mode=mode,skill_reload_mode='off',history_path=str(root/'evolve_history.jsonl'),processed_log_path=str(root/'processed.json'),debug_dump_dir=str(root/'debug'))
engine=EvolveServer(cfg)
engine._llm._client=engine._llm._client.with_options(timeout=60.0,max_retries=0)
(root/'evolve_once.executed.py').write_text(Path(__file__).read_text())
original=engine._llm.chat
calls=[]
async def observed(messages,**kw):
 kw['max_tokens']=min(int(kw.get('max_tokens',12000)),12000)
 i=len(calls)+1;rec={'index':i,'messages':messages,'started_at':time.time(),'max_tokens':kw['max_tokens']};calls.append(rec);write(root/'llm-calls.json',calls)
 try:
  response=await original(messages,extra_body={'thinking':{'type':'disabled'}},**kw);rec['response']=response;return response
 except Exception as e:rec['error']=type(e).__name__;raise
 finally:rec['elapsed_sec']=time.time()-rec['started_at'];write(root/'llm-calls.json',calls)
engine._llm.chat=observed
summary=asyncio.run(engine.run_once());write(root/'evolution.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2))
