import pytest
from conftest import dataset, analysis

DATA=b'Year,Total assets ($B),Revenue ($B)\n2022,50.43,23.18\n2021,53.6,23.22\n2020,52.62,19.2\n'

@pytest.mark.parametrize('question,expected',[
    ('What is the difference between Total Assets between 2021 and 2022?',-3.17),
    ('difference in total assets from 2021 to 2022',-3.17),
    ('Rate of growth for Total Assets between 2021 and 2022',-3.17/53.6*100),
    ('What is the growth rate of revenue from 2020 to 2021?',(23.22-19.2)/19.2*100),
])
def test_numeric_year_comparisons(system,question,expected):
    did=dataset(system,[('files',('financials.csv',DATA))]);aid=analysis(system,did,question)
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='answered',result
    metric=next(m for m in result['metrics'] if m['id']=='metric_value')
    assert metric['value']==pytest.approx(expected)
    assert '−' in result['narrative']
    assert result['verification']['status']=='passed'
    assert metric['unit']=='percent' if 'growth' in question.lower() else metric['unit']=='billion dollars'


def test_unspecified_growth_period_can_be_confirmed(system):
    did=dataset(system,[('files',('financials.csv',DATA))]);aid=analysis(system,did,'Rate of growth for Total Assets')
    client,worker,*_=system;state=client.get(f'/api/v1/analyses/{aid}').json()
    assert state['status']=='needs_clarification'
    c=state['clarification'];assert c['choices'][0]['label']=='2021 to 2022'
    client.post(f'/api/v1/analyses/{aid}/clarifications',json={'clarification_id':c['id'],'text':'2020 to 2021'})
    worker.run_once();result=client.get(f'/api/v1/analyses/{aid}/result').json()
    assert next(m for m in result['metrics'] if m['id']=='metric_value')['value']==pytest.approx((53.6-52.62)/52.62*100)


def test_missing_year_is_refused_not_zero(system):
    did=dataset(system,[('files',('financials.csv',DATA))]);aid=analysis(system,did,'difference in total assets from 2019 to 2022')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='refused' and not result['metrics']


def test_growth_evidence_has_source_years_and_readable_numbers(system):
    did=dataset(system,[('files',('financials.csv',b'Year,Revenue ($B)\n2002,15.4\n2022,23.18\n'))])
    aid=analysis(system,did,'Rate of growth for revenue between 2002 and 2022')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    explanation=result['narrative']
    assert 'Source column: Revenue ($B); reporting field: Year' in explanation
    assert '2002 total: 15.4 billion dollars' in explanation
    assert '2022 total: 23.18 billion dollars' in explanation
    assert 'Growth = 7.78 ÷ 15.4 × 100 = 50.5195%' in explanation
    assert '7.779999999' not in explanation
