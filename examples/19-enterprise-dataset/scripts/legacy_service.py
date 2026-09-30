"""教学旧代码服务：只根据同一输入快照确定性核查，不读取 oracle。"""
from readonly_tools import read_table

def run(workspace):
    request=read_table(workspace,'request');orders=read_table(workspace,'orders');ledger=read_table(workspace,'ledger');channel=read_table(workspace,'channel'); rows=[]
    for order in orders:
        mid,oid=order['merchant_id'],order['order_id']
        if mid!=request['merchant_id'] or oid not in request['order_ids'] or order['currency']!=request['currency']: continue
        matches=lambda e:e['merchant_id']==mid and e['order_id']==oid and e['currency']==order['currency'] and e['occurred_at']<=request['as_of']
        entries={e['entry_id']:e for e in ledger if matches(e) and e['state']=='posted'}
        paid=sum(e['amount_cents']*(1 if e['kind']=='payment' else -1) for e in entries.values()); due=order['due_cents']
        receipts=[r for r in channel if matches(r) and r['state'] in ['accepted','processing']]
        state='overpaid' if paid>due else 'paid' if paid==due else 'partial' if paid>0 else 'pending' if receipts else 'unpaid'
        evidence=sorted(entries) if entries else sorted(r['receipt_id'] for r in receipts) if state=='pending' else []
        rows.append(dict(merchant_id=mid,order_id=oid,effective_paid_cents=paid,difference_cents=due-paid,status=state,evidence_ids=evidence,explanation='依据任务时点的账务及渠道快照核查。'))
    return dict(orders=rows)
