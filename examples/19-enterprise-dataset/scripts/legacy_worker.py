#!/usr/bin/env python3
import sys,json,time
from pathlib import Path
from legacy_service import run
p=json.loads(Path(sys.argv[1]).read_text());start=time.monotonic()
try:
    final=json.dumps(run(p['workspace']),ensure_ascii=False)
    result=dict(engine='course-legacy',exit_code=0,turns=1,duration_ms=int((time.monotonic()-start)*1000),final_message=final,transcript=p['messages']+[dict(role='assistant',content=final)])
except Exception as e: result=dict(exit_code=1,final_message='旧服务执行失败：'+type(e).__name__,transcript=[])
Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
