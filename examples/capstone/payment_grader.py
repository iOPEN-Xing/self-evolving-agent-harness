"""综合实践补充队列条件核查；保留共享评分器的金额与状态核验。"""
from pathlib import Path
import copy,json,re


def install():
    from assembly.eval import judge,runner,policy
    if getattr(judge,'_capstone_route_installed',False): return
    native_answer,native_selfcheck=judge.judge_answer,judge.judge_self_check
    def answer(case,text):
        result=native_answer(case,text)
        order_id=str(case.get('order_id',case.get('case_id',''))).upper()
        orders=json.loads((judge.SCENARIOS_DIR/'orders.json').read_text())
        order=orders.get(order_id,{})
        eligible=any(t.get('status')=='processing' and t.get('channel_acceptance')=='success' for t in order.get('transactions',[]))
        # 只评价当前建议；“若以后确认受理再进入……”不算已满足条件。
        current=judge._present_facts(judge._normalise(text))
        mentions=[c for c in judge._clauses(current) if 'payops-confirm-17' in c]
        suggested=any(not re.search(r'(?:不|不能|不应|不要|不得|禁止)(?:进入|转入|送入|加入|使用|走).{0,12}payops-confirm-17',c) for c in mentions)
        route_ok=not suggested or eligible
        result.key_points['route_condition_supported']=route_ok
        if not route_ok:
            result.passed=False;result.business_correct=False
            result.reason+='；队列建议缺少同笔交易processing且渠道已受理的依据'
        return result
    def selfcheck():
        check=native_selfcheck()
        cases={c['case_id']:c for c in json.loads((judge.SCENARIOS_DIR/'cases.json').read_text())}
        samples=[
            ('known_bad:route_without_acceptance','ORD-A1','应付1000元，TXN-A600元成功，TXN-B400元处理中，差额400元，尚未付清，等待最终确认。转入payops-confirm-17。',False),
            ('known_bad:route_unknown_acceptance','ORD-A1','应付1000元，TXN-A600元成功，TXN-B400元处理中，差额400元，尚未付清，等待最终确认。无法确认渠道受理，但建议进入payops-confirm-17。',False),
            ('valid_paraphrase:route_supported','ORD-A2','金额800元，TXN-X仍在processing，渠道已受理成功，尚未最终到账，等待确认。进入payops-confirm-17。',True),
            ('valid_paraphrase:future_route','ORD-A1','应付1000元，TXN-A600元成功，TXN-B400元处理中，差额400元，尚未付清，等待最终确认。若未来确认渠道已受理，再进入payops-confirm-17。',True),
        ]
        good=True
        for cid,oid,text,want in samples:
            case=copy.deepcopy(cases[oid]);case.update(case_id=cid,order_id=oid)
            row=answer(case,text);check.details.append(row);good &= row.passed is want
        check.known_bad_rejected &= good
        check.valid_paraphrase_accepted &= good
        return check
    judge.judge_answer=runner.judge_answer=policy.judge_answer=answer
    judge.judge_self_check=policy.judge_self_check=selfcheck
    # 本轮记录的是同订单新问法，不将“留出”这个内部类型名作为泛化结论。
    judge.EVALUATOR_VERSION=runner.EVALUATOR_VERSION=policy.EVALUATOR_VERSION='capstone-payment-route-v1'
    judge._capstone_route_installed=True


def verify():
    install()
    from assembly.eval.judge import judge_self_check
    check=judge_self_check()
    if not check.passed:
        raise RuntimeError('评分器自测未通过：'+','.join(r.case_id for r in check.details if r.case_id.startswith('valid_paraphrase:') and not r.passed))
    print(f'综合实践评分器自测通过：{len(check.details)}项，未调用模型。')
    return check
