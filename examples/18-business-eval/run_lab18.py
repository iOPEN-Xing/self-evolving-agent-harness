#!/usr/bin/env python3
"""比较 2 套练习指令：4 个相同用例、8 次独立真实请求与结构化评分。

不加载 Hermes Skill，不调用支付接口。退出 0 表示请求和评测完整完成，
业务或格式失败照实保留，不要求 2 版都全过，也不预设修改必然改善。
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'output'
MODEL = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
DATASET_VERSION = 'payment-cases-v3'
EXPECTED_VERSION = 'payment-expected-v3'
SCORING_VERSION = 'structured-payment-zh-v2'
TEMPERATURE = 0.1

BASE_INSTRUCTION = '你是付款核查助手。直接、简洁地说明付款结论，不要展开分析过程。'
VERSIONS = {
    'before': BASE_INSTRUCTION,
    'after': BASE_INSTRUCTION + '\n同时核对商户号与订单号，遍历关联交易，只累计成功交易，处理中和失败不计入；按交易号去重。计算有效已付与差额，保留引用交易号，不把另一商户的同号订单算进来。',
}
OUTPUT_CONTRACT = '''
材料是构造的支付记录快照，金额单位为元，不代表执行了真实查询。
只返回 JSON 对象：merchant_id、order_id 为字符串；amount_due、valid_paid、difference 为整数；
payment_status 为 paid、partial 或 processing；transaction_ids 为计入有效已付的交易号数组；
answer_zh 为至多 80 字符、不换行、不用列表的中文结论。
中文结论必须明确写出有效已付金额和差额，并用“已付清/已全额支付”“未付清/部分付款”“处理中/尚无成功付款”之一说明状态。
中文结论与字段必须一致，不声称使用工具。
'''
CASES = [
    {'id':'T01','name':'成功付款','question':'核对 M01 的 O01 付款情况。',
     'facts':{'merchant_id':'M01','order_id':'O01','amount_due':1200,
              'transactions':[{'merchant_id':'M01','order_id':'O01','transaction_id':'TX01','amount':500,'status':'success'},
                              {'merchant_id':'M01','order_id':'O01','transaction_id':'TX02','amount':700,'status':'success'}]}},
    {'id':'T02','name':'部分付款','question':'核对 M01 的 O02 付款情况。',
     'facts':{'merchant_id':'M01','order_id':'O02','amount_due':1500,
              'transactions':[{'merchant_id':'M01','order_id':'O02','transaction_id':'TX03','amount':1000,'status':'success'},
                              {'merchant_id':'M01','order_id':'O02','transaction_id':'TX04','amount':500,'status':'processing'}]}},
    {'id':'T03','name':'处理中','question':'核对 M01 的 O03 付款情况。',
     'facts':{'merchant_id':'M01','order_id':'O03','amount_due':800,
              'transactions':[{'merchant_id':'M01','order_id':'O03','transaction_id':'TX05','amount':800,'status':'processing'}]}},
    {'id':'T04','name':'跨商户同号','question':'核对 M01 的 O04，另一商户同号记录不能计入。',
     'facts':{'merchant_id':'M01','order_id':'O04','amount_due':800,
              'transactions':[{'merchant_id':'M01','order_id':'O04','transaction_id':'TX06','amount':300,'status':'success'},
                              {'merchant_id':'M02','order_id':'O04','transaction_id':'TX07','amount':800,'status':'success'}]}},
]
EXPECTED = {
    'T01':dict(merchant_id='M01',order_id='O01',amount_due=1200,valid_paid=1200,difference=0,payment_status='paid',transaction_ids=['TX01','TX02']),
    'T02':dict(merchant_id='M01',order_id='O02',amount_due=1500,valid_paid=1000,difference=500,payment_status='partial',transaction_ids=['TX03']),
    'T03':dict(merchant_id='M01',order_id='O03',amount_due=800,valid_paid=0,difference=800,payment_status='processing',transaction_ids=[]),
    'T04':dict(merchant_id='M01',order_id='O04',amount_due=800,valid_paid=300,difference=500,payment_status='partial',transaction_ids=['TX06']),
}


def chinese_checks(answer, expected):
    # 保守核对约定的中文表达，不以任意金额出现或状态子串命中作为通过。
    if not isinstance(answer, str):
        return {'status':False, 'valid_paid':False, 'difference':False}
    patterns = {'paid':r'已付清|已经付清|已全额支付|已足额支付',
                'partial':r'未付清|尚未付清|未全额支付|部分付款',
                'processing':r'处理中|尚无成功付款|暂无成功付款'}
    # “未付清”含“付清”，因此肯定表达只匹配带“已”的完整片段。
    statuses = {k:bool(re.search(v, answer)) for k,v in patterns.items()}
    status = expected['payment_status']
    # partial 可以描述另一笔交易处理中，processing 不能自称已付清或部分付款。
    valid_status = statuses[status] and not statuses['paid'] if status != 'paid' else statuses['paid'] and not (statuses['partial'] or statuses['processing'])
    if status == 'processing':
        valid_status = valid_status and not statuses['partial']
    def amounts(pattern, wanted):
        found = re.findall(pattern + r'\s*(?:为|是|共|合计)?\s*([0-9]+)\s*元', answer)
        return bool(found) and all(int(x) == wanted for x in found)
    return {'status':bool(valid_status),
            'valid_paid':amounts(r'(?:有效已付|成功付款|成功支付|已支付|已付)', expected['valid_paid']),
            'difference':amounts(r'(?:差额|尚差|还差|尚欠|剩余应付)', expected['difference']),
            'no_negated_positive':not bool(re.search(r'(?:不|未|并非|不是|不能|尚未)\s*(?:已付清|已全额支付|已足额支付)',answer))}


def save(path, value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    path.write_text(text, encoding='utf-8')
    return hashlib.sha256(text.encode()).hexdigest()


def score(case_id, raw):
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return {'schema_pass':False, 'business_pass':False, 'format_pass':False,
                'overall_pass':False, 'parse_error':str(exc), 'answer_zh':None}
    if not isinstance(data, dict):
        return {'schema_pass':False, 'business_pass':False, 'format_pass':False,
                'overall_pass':False, 'parse_error':'响应不是 JSON 对象', 'answer_zh':None}
    expected = EXPECTED[case_id]
    checks = {}
    for field, wanted in expected.items():
        actual = data.get(field)
        if field == 'transaction_ids':
            checks[field] = (isinstance(actual, list) and all(type(x) is str for x in actual)
                             and len(actual) == len(set(actual)) and sorted(actual) == sorted(wanted))
        else:
            checks[field] = type(actual) is type(wanted) and actual == wanted
    answer = data.get('answer_zh')
    expected_keys = set(expected) | {'answer_zh'}
    schema_pass = set(data) == expected_keys and type(answer) is str
    format_checks = {
        'nonempty': type(answer) is str and bool(answer.strip()),
        'max_80_characters': type(answer) is str and len(answer) <= 80,
        'single_line': type(answer) is str and '\n' not in answer and '\r' not in answer,
        'no_list': type(answer) is str and not re.search(r'^\s*(?:[-*•]|\d+[.、)])\s*', answer),
        'no_em_dash': type(answer) is str and '\u2014' not in answer,
    }
    zh_checks = chinese_checks(answer, expected)
    business_pass = all(checks.values()) and all(zh_checks.values())
    format_pass = all(format_checks.values())
    return {'schema_pass':schema_pass, 'business_pass':business_pass, 'format_pass':format_pass,
            'overall_pass':schema_pass and business_pass and format_pass,
            'business_fields':checks, 'chinese_consistency':zh_checks, 'format_checks':format_checks, 'answer_zh':answer,
            'parsed':data, 'expected':expected}


def main():
    from selftest import run as scoring_selftest
    scoring_selftest()
    started = time.perf_counter()
    OUTPUT.mkdir(exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='run-', dir=OUTPUT))
    for name in list(os.environ):
        if name.lower().endswith('_proxy'):
            os.environ.pop(name, None)
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        print('需要设置 DEEPSEEK_API_KEY', file=sys.stderr)
        return 1
    from openai import OpenAI
    hashes = {'dataset':save(run_dir/'dataset.json', CASES),
              'expected':save(run_dir/'expected.json', EXPECTED),
              'scoring_source':save(run_dir/'scoring_source.py', Path(__file__).read_text(encoding='utf-8'))}
    prompts = {version:instruction + OUTPUT_CONTRACT for version,instruction in VERSIONS.items()}
    hashes['prompts'] = {version:save(run_dir/f'prompt-{version}.txt', prompt) for version,prompt in prompts.items()}
    results = []
    print('2 套练习指令，各运行 4 个相同用例；每次请求只包含当前用例。', flush=True)
    with OpenAI(api_key=api_key, base_url=BASE_URL, timeout=120, max_retries=0) as client:
        for version,prompt in prompts.items():
            for case in CASES:
                request_dir = run_dir / version / case['id']
                request_dir.mkdir(parents=True)
                user_message = json.dumps({'question':case['question'], 'facts':case['facts'],
                                           'source':'构造的支付记录快照'}, ensure_ascii=False)
                save(request_dir/'input.txt', user_message)
                row = {'version':version, 'case_id':case['id'], 'name':case['name'],
                       'independent_request':True,
                       'request_succeeded':False}
                request_started = time.perf_counter()
                try:
                    response = client.chat.completions.create(
                        extra_body={"thinking": {"type": "disabled"}},
                        model=MODEL, temperature=TEMPERATURE, max_tokens=2000,
                        messages=[{'role':'system','content':prompt},{'role':'user','content':user_message}])
                    raw = response.choices[0].message.content or ''
                    save(request_dir/'response.txt', raw)
                    row.update(request_succeeded=True, response_model=response.model,
                               response_id=response.id, raw_response=raw, score=score(case['id'],raw))
                    print(f"{version}/{case['id']}：业务={row['score']['business_pass']}，格式={row['score']['format_pass']}；{row['score']['answer_zh']}", flush=True)
                except Exception as exc:
                    message = f'{type(exc).__name__}: {exc}'
                    for key in (api_key,os.environ.get('DEEPSEEK_API_KEY')):
                        if key:
                            message = message.replace(key,'[密钥已隐藏]')
                    row['error'] = message
                    print(f"{version}/{case['id']}：请求或评分未完成，{message}", flush=True)
                row['elapsed_seconds'] = round(time.perf_counter()-request_started,3)
                save(request_dir/'result.json',row)
                results.append(row)
    version_results = {}
    for version in VERSIONS:
        subset = [row for row in results if row['version']==version]
        version_results[version] = {metric:sum(row.get('score',{}).get(metric,False) for row in subset)
                                   for metric in ('business_pass','format_pass','overall_pass')}
        version_results[version]['total'] = len(subset)
    comparisons = []
    for case in CASES:
        pair = {row['version']:row for row in results if row['case_id']==case['id']}
        item = {'case_id':case['id']}
        for metric in ('business_pass','format_pass','overall_pass'):
            if not all(row.get('request_succeeded') and 'score' in row for row in pair.values()):
                change = '无法比较'
            else:
                old,new = (pair[v]['score'][metric] for v in ('before','after'))
                change = '本次由失败转为通过' if not old and new else '本次由通过转为失败' if old and not new else '本次结果相同'
            item[metric] = change
        comparisons.append(item)
    complete = len(results)==8 and all(row.get('request_succeeded') and 'score' in row for row in results)
    report = {'timestamp':datetime.now(timezone.utc).isoformat(), 'model':MODEL,'temperature':TEMPERATURE,
              'dataset_version':DATASET_VERSION,'expected_version':EXPECTED_VERSION,'scoring_version':SCORING_VERSION,
              'hashes':hashes,'prompt_versions':list(VERSIONS), 'run_directory':str(run_dir.relative_to(HERE)),
              'constructed_cases':True,'hermes_runtime_used':False,'payment_system_queried':False,
              'request_count':len(results),'successful_requests':sum(row['request_succeeded'] for row in results),
              'evaluation_complete':complete,'exit_code':0 if complete else 1,
              'versions':version_results,'comparisons':comparisons,'results':results,
              'elapsed_seconds':round(time.perf_counter()-started,3),
              'limitation':'2 套练习指令各运行 1 次小样本，不能证明长期收益或泛化；中文核查只覆盖约定表达，未识别表达会拒绝；仍需人工检查报告，不能把它当作通用语义判定器。'}
    save(run_dir/'eval_report.json',report)
    save(OUTPUT/'eval_report.json',report)
    print(json.dumps(version_results,ensure_ascii=False),flush=True)
    print(f"脚本内耗时：{report['elapsed_seconds']} 秒；退出码：{report['exit_code']}",flush=True)
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
