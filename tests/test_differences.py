import pytest
from conftest import dataset, analysis


@pytest.mark.parametrize('question,value', [
    ('What is the difference between Revenue and Cost?', 35),
    ('subtract Cost from Revenue', 35),
    ('What is the difference in revenue between the last quarter and the previous quarter?', -40),
    ('What is the change in revenue from the previous quarter to the latest quarter?', -40),
    ('difference of Revenue from 2025-06 to 2025-09 using Quarter', -40),
    ('change in Revenue from Q2 2025 to Q3 2025 using Quarter', -40),
    ('What is the difference between revenue in the last quarter and previous quarter?', -40),
    ('What is the percentage change in revenue from the previous quarter to the latest quarter?', -40),
])
def test_difference_questions_and_explanations(system, question, value):
    did=dataset(system, [('files', ('comparison.csv', b'Quarter,Revenue,Cost,Region\n2025-06-30,100,80,West\n2025-09-30,60,45,West\n'))])
    aid=analysis(system,did,question)
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='answered', result
    assert next(m for m in result['metrics'] if m['id']=='metric_value')['value']==value
    assert result['verification']['status']=='passed'
    assert '−' in result['narrative']
    assert ('100' in result['narrative'] and '60' in result['narrative']) if 'quarter' in question or '2025' in question else '160 − 125 = 35' in result['narrative']


def test_grouped_difference_allows_zero_second_total(system):
    did=dataset(system,[('files',('comparison.csv',b'Revenue,Cost,Region\n10,0,West\n5,20,East\n'))])
    aid=analysis(system,did,'difference between Revenue and Cost by Region')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='answered', result
    assert {r['group_value']:r['value'] for r in result['tables'][0]['rows']}=={'West':10,'East':-15}


def test_missing_comparison_period_is_not_zero(system):
    did=dataset(system,[('files',('comparison.csv',b'Quarter,Revenue\n2025-09-30,60\n'))])
    aid=analysis(system,did,'change in revenue from previous quarter to latest quarter')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='refused'
    assert not result['metrics']


def test_difference_does_not_ignore_extra_requested_calculation(system):
    did=dataset(system,[('files',('comparison.csv',b'Revenue,Cost\n10,5\n'))])
    aid=analysis(system,did,'difference between Revenue and Cost and predict next year')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='refused'


def test_currency_difference_preserves_units(system):
    did=dataset(system,[('files',('comparison.csv',b'Revenue,Cost\n$100,$40\n'))])
    aid=analysis(system,did,'difference between Revenue and Cost')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['metrics'][-1]['formatted_value']=='$60'
    assert '$100 − $40 = $60' in result['narrative']


def test_mixed_currencies_are_not_subtracted(system):
    did=dataset(system,[('files',('comparison.csv',b'Revenue,Cost\nUSD 100,EUR 40\n'))])
    aid=analysis(system,did,'difference between Revenue and Cost')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='refused'
    assert 'units' in result['narrative']
