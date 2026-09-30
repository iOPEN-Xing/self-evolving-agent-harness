"""从原始回执重算异常线索指标，不修改最初汇总或任何原答。"""
from common import *
root=Path(sys.argv[1]).resolve();rows=[]
for d in sorted(root.glob('task-*')):
 t=read(d/'task.json');snap=read(d/'snapshot.json');obs={}
 for e in t['commands']:
  k=e['command'].split()[-1]
  if e['exit_code']==0:
   value=json.loads(e['stdout']);assert value==snap[k];obs[k]=value
 signal=(obs.get('provider',{}).get('latency_ms',0)>1000) if t['case'].startswith('upstream') else (obs.get('pool',{}).get('waiting',0)>0)
 rows.append({'case':t['case'],'signal_observed':signal,'observed':obs,'snapshot_sha256':sha((d/'snapshot.json').read_bytes()),'task_sha256':sha((d/'task.json').read_bytes()),'tool_receipts_match_snapshot':True})
write(root/'signal-audit.json',{'metric':'指定分支异常线索被真实工具取回；不计为根因正确率','signal_observed':sum(r['signal_observed'] for r in rows),'total':len(rows),'rows':rows,'unique_snapshot_count':len({r['snapshot_sha256'] for r in rows}),'overclaim_review':'三段上游原答把超过阈值直接判为根因。证据不足，未计为已确认根因。全部快照都含连接池异常，已执行部分不能排除其他问题。','metric_note':'signal_observed只检查真实回执中的异常；不推断根因。','bypass':read(root/'bypass.json')})
print('线索审计',sum(r['signal_observed'] for r in rows),len(rows))
