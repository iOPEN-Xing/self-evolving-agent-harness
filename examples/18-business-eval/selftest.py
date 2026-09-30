"""评分器自测：不调用模型、不读取凭证。"""
import json
from run_lab18 import EXPECTED, score


def run():
    checked = 0
    def check(case_id, answer, passed, **updates):
        nonlocal checked
        value = dict(EXPECTED[case_id], answer_zh=answer)
        value.update(updates)
        result = score(case_id, json.dumps(value, ensure_ascii=False))
        assert result['overall_pass'] is passed, (case_id, value, result)
        checked += 1
    check('T01', '有效已付1200元，差额0元，已付清。', True)
    check('T01', '成功付款合计1200元，尚差0元，已全额支付。', True)
    check('T02', '有效已付1000元，差额500元，尚未付清，另一笔处理中。', True)
    check('T03', '有效已付0元，差额800元，处理中。', True)
    check('T04', '成功支付300元，尚欠500元，部分付款。', True)
    # 结构字段正确，中文状态反转不得整体通过。
    check('T01', '有效已付1200元，差额0元，尚未付清。', False)
    check('T02', '有效已付1000元，差额500元，已付清。', False)
    check('T03', '有效已付0元，差额800元，已全额支付。', False)
    check('T04', '有效已付300元，差额500元，已付清。', False)
    check('T01', '有效已付1200元，差额0元，已付清。', False, payment_status='partial')
    check('T02', '有效已付1500元，差额0元，未付清。', False)
    check('T02', '有效已付1000元，差额500元，未付清。', False, valid_paid=1500)
    check('T04', '有效已付300元，差额500元，部分付款。', False, merchant_id='M02', transaction_ids=['TX07'])
    check('T04', '有效已付300元，差额500元，部分付款。', False, transaction_ids=['TX06','TX07'])
    check('T01', '有效已付1200元，差额0元，并非已付清。', False)
    check('T01', '有效已付1200元，差额0元，已付清但未付清。', False)
    assert not score('T01', 'not-json')['overall_pass']
    print(f'评分器自测通过：{checked + 1} 项，无模型请求。')
    return checked + 1


if __name__ == '__main__':
    run()
