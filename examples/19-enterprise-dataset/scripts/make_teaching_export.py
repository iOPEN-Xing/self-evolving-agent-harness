#!/usr/bin/env python3
"""创建明确标注为教学材料的导出样例；不连接或冒充 Langfuse 租户。"""
import json, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VERSION='2026-09-26T00:00:00Z'
def dump(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def order(mid,oid,due,currency='CNY'): return dict(merchant_id=mid,order_id=oid,due_cents=due,currency=currency)
def entry(eid,mid,oid,amount,kind='payment',state='posted',at='2026-09-24T09:00:00Z',currency='CNY'):
    return dict(entry_id=eid,merchant_id=mid,order_id=oid,amount_cents=amount,kind=kind,state=state,occurred_at=at,currency=currency)
def receipt(rid,mid,oid,state='accepted',at='2026-09-24T09:00:00Z'):
    return dict(receipt_id=rid,merchant_id=mid,order_id=oid,state=state,occurred_at=at,currency='CNY')
def answer(mid,oid,paid,diff,status,evidence):
    return dict(merchant_id=mid,order_id=oid,effective_paid_cents=paid,difference_cents=diff,status=status,evidence_ids=evidence)
# 答案是教学作者预先给定的业务事实，未调用旧服务生成。
specs=[
('普通到账',[order('m01','a01',12000)],[entry('l01','m01','a01',12000)],[],[answer('m01','a01',12000,0,'paid',['l01'])]),
('受理未终结',[order('m02','a02',10000)],[],[receipt('r02','m02','a02')],[answer('m02','a02',0,10000,'pending',['r02'])]),
('部分付款',[order('m03','a03',10000)],[entry('l03','m03','a03',4000)],[],[answer('m03','a03',4000,6000,'partial',['l03'])]),
('跨商户同号',[order('m04','same',10000)],[entry('l04','m04','same',3000),entry('l04x','other','same',9000)],[],[answer('m04','same',3000,7000,'partial',['l04'])]),
('重复账务事件',[order('m05','a05',10000)],[entry('l05','m05','a05',10000),entry('l05','m05','a05',10000)],[],[answer('m05','a05',10000,0,'paid',['l05'])]),
('退款后净额',[order('m06','a06',10000)],[entry('l06','m06','a06',10000),entry('r06','m06','a06',3000,kind='refund')],[],[answer('m06','a06',7000,3000,'partial',['l06','r06'])]),
('付款失败',[order('m07','a07',6000)],[entry('l07','m07','a07',6000,state='failed')],[receipt('r07','m07','a07','failed')],[answer('m07','a07',0,6000,'unpaid',[])]),
('超额付款',[order('m08','a08',10000)],[entry('l08','m08','a08',13000)],[],[answer('m08','a08',13000,-3000,'overpaid',['l08'])]),
('多订单范围',[order('m09','a09',6000),order('m09','b09',8000)],[entry('l09','m09','a09',6000)],[receipt('r09','m09','b09','processing')],[answer('m09','a09',6000,0,'paid',['l09']),answer('m09','b09',0,8000,'pending',['r09'])]),
('查询截止后到账排除',[order('m10','a10',10000)],[entry('l10','m10','a10',10000,at='2026-09-24T10:15:00Z')],[receipt('r10','m10','a10')],[answer('m10','a10',0,10000,'pending',['r10'])]),
('跨币种记录',[order('m11','a11',10000)],[entry('l11','m11','a11',10000,currency='USD')],[],[answer('m11','a11',0,10000,'unpaid',[])]),
('多笔付款与漏单',[order('m12','a12',10000),order('m12','b12',2000)],[entry('l12a','m12','a12',2500),entry('l12b','m12','a12',2500)],[],[answer('m12','a12',5000,5000,'partial',['l12a','l12b']),answer('m12','b12',0,2000,'unpaid',[])])]
items=[]; traces=[]; events=[]
for n,(stratum,orders,ledger,channel,expected) in enumerate(specs,1):
    cid=f'payment-{n:03}'; request=dict(request_id=f'req-{n:03}',merchant_id=orders[0]['merchant_id'],order_ids=[o['order_id'] for o in orders],as_of='2026-09-24T10:00:00Z',task_started_at='2026-09-24T10:30:00Z',snapshot_at='2026-09-24T10:30:00Z',currency='CNY')
    snap=dict(request=request,orders=orders,ledger=ledger,channel=channel)
    for name,data in snap.items(): dump(ROOT/'source/snapshots'/cid/(name+'.json'),data)
    accepted=int(n%3!=0)
    item=dict(id=cid,sourceTraceId=f'teaching-trace-{n:03}',sourceObservationId=f'teaching-root-{n:03}',input=dict(task='核对 input/ 中本次请求范围的支付状态，逐个订单返回约定 JSON 报告。',snapshot=cid),expectedOutput=dict(orders=expected),metadata=dict(teaching_only=True,business_type='payment_report',stratum=stratum,session_id=f'teaching-session-{n:03}',request_id=request['request_id'],initial_request_complete=True,user_followups=0,requires_user_choice=False,task_finished=True,ground_mature=True,ground_disputed=False,truth_source=f'teaching-ledger-facts:{cid}',truth_valid_at=request['as_of'],snapshot_hashes={k:hashlib.sha256((ROOT/'source/snapshots'/cid/(k+'.json')).read_bytes()).hexdigest() for k in snap},historical_feedback=dict(accepted=accepted,mature=True,event_id=f'teaching-event-{n:03}',window_hours=24,source='教学业务反馈表',observed_at='2026-09-25T11:00:00Z'),split='teaching_evaluation'))
    items.append(item)
    traces.append(dict(id=item['sourceTraceId'],session_id=item['metadata']['session_id'],request_id=request['request_id'],provenance='教学作者构造的结构示例，非租户采集',root_observation=dict(id=item['sourceObservationId'],name='Hermes turn',type='chain'),observations=[dict(type='generation',name='LLM请求'),dict(type='tool',name='payment_read',input={'table':'orders'}),dict(type='tool',name='payment_read',input={'table':'ledger'})],input=request,output=dict(orders=expected)))
    events.append(dict(event_id=f'teaching-event-{n:03}',request_id=request['request_id'],trace_id=item['sourceTraceId'],value=accepted,name='accepted',mature=True,teaching_only=True,source='教学作者构造的历史反馈，不属于新版结果'))
for cid,reason in [('excluded-interactive','interactive'),('excluded-choice','choice'),('excluded-snapshot','missing_snapshot'),('excluded-ground','immature_ground')]:
    x=json.loads(json.dumps(items[0])); x['id']=cid; x['sourceTraceId']='teaching-'+cid
    if reason=='interactive': x['metadata']['user_followups']=1; x['metadata']['initial_request_complete']=False
    if reason=='choice': x['metadata']['requires_user_choice']=True
    if reason=='missing_snapshot': x['input']['snapshot']='not-preserved'
    if reason=='immature_ground': x['metadata']['ground_mature']=False; x['metadata']['historical_feedback']['mature']=False; x['expectedOutput']=None
    items.append(x)
dump(ROOT/'source/langfuse-export.teaching.json',dict(schema='course-langfuse-export-v1',dataset_name='payment-report-teaching',dataset_version=VERSION,provenance=dict(kind='教学作者构造',is_real_langfuse_export=False,description='沿用 dataset item 常见字段的教学 JSON；附加 schema/provenance 是课程字段。没有从真实租户采集。',documentation='https://langfuse.com/docs/evaluation/experiments/datasets'),items=items))
dump(ROOT/'source/traces.teaching.json',traces); dump(ROOT/'source/business-events.teaching.json',events)
print('已生成 12 条可重放教学记录、4 条排除样例；不计为真实采集验收。')
