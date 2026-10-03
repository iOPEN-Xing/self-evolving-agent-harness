import copy
import json

import pytest

from conftest import ROOT, load_module

GRADER = load_module('enterprise_grader', 'examples/19-enterprise-dataset/scripts/grader.py')
ORACLE_DIR = ROOT / 'examples/19-enterprise-dataset/evals/grader-only'


@pytest.mark.parametrize('path', sorted(ORACLE_DIR.glob('*.json')), ids=lambda p: p.stem)
def test_each_packaged_oracle_is_accepted(path):
    oracle = json.loads(path.read_text())
    assert GRADER.grade(json.dumps(oracle), oracle)['passed']


@pytest.fixture
def oracle():
    return json.loads((ORACLE_DIR / 'payment-009.json').read_text())


@pytest.mark.parametrize('mutation', ['status', 'merchant', 'amount', 'boolean_amount',
                                     'missing_order', 'duplicate_order', 'evidence'])
def test_business_errors_cannot_pass(oracle, mutation):
    answer = copy.deepcopy(oracle)
    order = answer['orders'][0]
    if mutation == 'status':
        order['status'] = 'unpaid' if order['status'] != 'unpaid' else 'paid'
    elif mutation == 'merchant':
        order['merchant_id'] = 'another-merchant'
    elif mutation == 'amount':
        order['effective_paid_cents'] += 1
    elif mutation == 'boolean_amount':
        order['effective_paid_cents'] = True
    elif mutation == 'missing_order':
        answer['orders'].pop()
    elif mutation == 'duplicate_order':
        answer['orders'].append(copy.deepcopy(order))
    else:
        order['evidence_ids'] = ['not-in-this-snapshot']
    assert GRADER.grade(json.dumps(answer), oracle)['passed'] is False


def test_duplicate_json_fields_cannot_hide_wrong_answer(oracle):
    raw = '{"orders":[],"orders":' + json.dumps(oracle['orders']) + '}'
    assert GRADER.grade(raw, oracle)['passed'] is False


def test_execution_failure_is_separate_from_business_failure(oracle):
    result = GRADER.grade(json.dumps(oracle), oracle, exit_code=124)
    assert result['passed'] is False and result['failure_kind'] == 'execution'
