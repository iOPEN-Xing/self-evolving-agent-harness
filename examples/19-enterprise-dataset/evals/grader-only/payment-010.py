#!/usr/bin/env python3
"""只评最终结果；不读取历史采纳率替候选计分。"""
import json,os,re
ALIASES={'paid':'paid','到账':'paid','已到账':'paid','pending':'pending','处理中':'pending','受理未终结':'pending','partial':'partial','部分付款':'partial','unpaid':'unpaid','未付款':'unpaid','overpaid':'overpaid','超额付款':'overpaid'}
def grade(final,oracle,exit_code=0):
    errors=[]
    if exit_code!=0: return dict(passed=False,errors=['运行退出码非零'],failure_kind='execution')
    try:
        text=final.strip()
        obj=json.loads(text); actual=obj['orders']; expected=oracle['orders']
        keys=lambda rows:[(r['merchant_id'],r['order_id']) for r in rows]
        ak,ek=keys(actual),keys(expected)
        if len(set(ak))!=len(ak): errors.append('重复订单')
        if set(ak)!=set(ek): errors.append('串单、漏单或多单')
        index={k:r for k,r in zip(ak,actual)}
        for key,want in zip(ek,expected):
            got=index.get(key)
            if got is None: continue
            for field in ['effective_paid_cents','difference_cents']:
                if type(got.get(field)) is not int or got[field]!=want[field]: errors.append(f'{key}:{field}错误')
            if ALIASES.get(got.get('status'))!=want['status']: errors.append(f'{key}:状态错误')
            evid=got.get('evidence_ids')
            if not isinstance(evid,list) or len(set(evid))!=len(evid) or set(evid)!=set(want['evidence_ids']): errors.append(f'{key}:依据不符')
    except (ValueError,TypeError,KeyError,AttributeError): errors.append('输出格式无效')
    return dict(passed=not errors,errors=errors,failure_kind=None if not errors else 'business')

ORACLE={'orders': [{'merchant_id': 'm10', 'order_id': 'a10', 'effective_paid_cents': 0, 'difference_cents': 10000, 'status': 'pending', 'evidence_ids': ['r10']}]}
if __name__ == "__main__":
    result=grade(os.environ.get("EVAL_FINAL_MESSAGE",""),ORACLE,int(os.environ.get("EVAL_EXIT_CODE","1")))
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
