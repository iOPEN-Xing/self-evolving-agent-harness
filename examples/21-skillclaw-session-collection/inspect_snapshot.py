"""真实子进程只读教学快照，不访问任何线上业务。"""
import sys,json
from pathlib import Path
p=Path(sys.argv[1]);key=sys.argv[2]
if key not in ('health','provider','pool'): raise ValueError('未知检查')
print(json.dumps(json.loads(p.read_text())[key],ensure_ascii=False))
