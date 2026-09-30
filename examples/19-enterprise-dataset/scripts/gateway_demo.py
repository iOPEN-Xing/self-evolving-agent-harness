#!/usr/bin/env python3
"""本地真实 HTTP 链路；教学请求与演示反馈，不代表真实用户 A/B。"""
import os,hashlib,json,sys,threading,subprocess,shutil,uuid
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];OUT=Path(os.environ.get('LECTURE19_OUTPUT_DIR',str(ROOT/'output/demo'))).resolve();EXP='teaching-replacement-v1'
requests={};events={};outbox=[];assignments={}
def assign(entity):
    bucket=int.from_bytes(hashlib.sha256((EXP+'|'+entity).encode()).digest()[:8],'big')/2**64
    return 'B' if bucket<0.5 else 'A'
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,obj):
        raw=json.dumps(obj,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path=='/report':
            entity=data['entity_id'];group=assign(entity);rid=data['request_id'];cid=data['case_id']
            if cid not in {f'payment-{n:03}' for n in range(1,13)}: return self.reply(400,{'error':'题目不在固定集合中'})
            work=OUT/'runtime'/('gateway-'+uuid.uuid4().hex);shutil.copytree(ROOT/'evals/fixtures/inputs'/cid,work)
            payload=dict(case_id=cid,workspace=str(work),messages=[dict(role='user',content='核对 input/ 中本次请求范围的支付状态，逐个订单返回约定 JSON 报告。')]);inp=work/'session-input.json';dest=work/'session-result.json';inp.write_text(json.dumps(payload,ensure_ascii=False))
            variant='legacy' if group=='A' else 'hermes'
            subprocess.run([sys.executable,str(ROOT/'scripts/engine.py'),'--variant',variant,'--input',str(inp),'--output',str(dest)],check=True,capture_output=True)
            result=json.loads(dest.read_text());record=dict(request_id=rid,trace_id='demo-trace-'+rid,entity_id=entity,experiment_id=EXP,group=group,assignment_probability=0.5,service_variant=variant,case_id=cid,is_demo=True,exit_code=result['exit_code'],response=result.get('final_message'),status='completed' if result['exit_code']==0 else 'not_completed')
            requests[rid]=record;assignments.setdefault(entity,set()).add(group)
            return self.reply(200,record)
        if self.path=='/business-result':
            rid=data.get('request_id');origin=requests.get(rid)
            if not origin or data.get('trace_id')!=origin['trace_id']: return self.reply(409,{'error':'事件与请求来源不一致'})
            if data.get('is_demo') is not True: return self.reply(400,{'error':'此教学入口仅接收明确标注的演示事件'})
            eid=data['event_id']
            if eid in events:
                if events[eid]!=data:return self.reply(409,{'error':'重复事件ID对应不同内容'})
                return self.reply(200,{'duplicate':True})
            events[eid]=data
            # 未完成的请求没有采纳结果；不能把未知填成0。
            if origin['status']=='completed' and data.get('mature') and data.get('accepted') in [0,1]:
                outbox.append(dict(trace_id=origin['trace_id'],request_id=rid,event_id=eid,name='demo_accepted',value=data['accepted'],group=origin['group'],source='明确标注的教学反馈',is_demo=True,delivery_status='仅写本地待发队列，未写Langfuse',dataset_item_updated=False))
            return self.reply(200,{'linked':True,'score_queued':origin['status']=='completed','dataset_item_updated':False})
        return self.reply(404,{'error':'未知路径'})
def post(url,path,obj):
    req=Request(url+path,data=json.dumps(obj).encode(),headers={'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(req,timeout=660) as response:return response.status,json.load(response)
    except HTTPError as e:return e.code,json.load(e)
def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}'
    # 仅为覆盖两臂挑教学实体；不估计采纳率，也不使用这组数量代表生产分流比例。
    chosen={}
    for n in range(100):
        entity=f'teaching-entity-{n}';chosen.setdefault(assign(entity),entity)
        if len(chosen)==2:break
    responses=[];acks=[]
    for group,entity in sorted(chosen.items()):
        for repeat in range(2):
            rid=f'{group.lower()}-request-{repeat}'
            code,response=post(url,'/report',dict(entity_id=entity,request_id=rid,case_id='payment-001'));assert code==200;responses.append(response)
            event=dict(event_id='demo-event-'+rid,request_id=rid,trace_id=response['trace_id'],accepted=1 if response['status']=='completed' else None,mature=response['status']=='completed',event_time='2026-09-26T00:00:00Z',window_hours=24,is_demo=True)
            code,ack=post(url,'/business-result',event);assert code==200;acks.append(ack)
            if group=='A' and repeat==0:
                code,duplicate=post(url,'/business-result',event);assert code==200 and duplicate['duplicate']
                bad=dict(event,event_id='bad-id',trace_id='unrelated');code,bad_ack=post(url,'/business-result',bad);assert code==409
    server.shutdown();server.server_close()
    result=dict(is_demo=True,stable_assignment_passed=all(len(v)==1 for v in assignments.values()),duplicate_event_deduplicated=True,mismatched_trace_rejected=True,requests=responses,events=list(events.values()),score_outbox=outbox,langfuse_delivery_performed=False,real_user_outcomes=0,online_noninferiority_completed=False,conclusion='只验证本地网关稳定分组、真实响应/失败记录与演示事件关联；不构成线上业务试验。')
    (OUT/'gateway-demo.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(stable=result['stable_assignment_passed'],requests=len(responses),completed=sum(r['status']=='completed' for r in responses),blocked=sum(r['exit_code']==78 for r in responses),events=len(events),queued_demo_scores=len(outbox),real_user_outcomes=0),ensure_ascii=False))
if __name__=='__main__':main()
