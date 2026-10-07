import io
from datetime import datetime
import pytest
from openpyxl import Workbook
from backend.ingest import currency_numbers
from conftest import dataset, analysis


def financial_data(system):
    book = Workbook(); sh = book.active
    sh.append(["Quarter", "Revenue", "Gross Profit"])
    sh.append([datetime(2024,12,31), "$1,000", "$200"])
    sh.append([datetime(2025,3,31), "$2,000", "$400"])
    sh.append([datetime(2025,6,30), "$3,000", "$600"])
    sh.append([datetime(2025,9,30), "$4,000", "$800"])
    stream=io.BytesIO(); book.save(stream)
    return dataset(system, [("files", ("quarterly.xlsx", stream.getvalue()))])


@pytest.mark.parametrize("question,expected", [("what is the revenue made in the last quarter",4000), ("what is the total revenue",10000), ("Revenue?",10000), ("Show me revenue",10000), ("What is revenue in the latest quarter?",4000), ("What is total revenue in the previous quarter?",3000)])
def test_financial_question_computes_values_with_units(system, question, expected):
    did=financial_data(system); aid=analysis(system,did,question)
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='answered',result
    assert result['metrics'][0]['value']==expected and result['metrics'][0]['unit']=='$'
    assert result['verification']['status']=='passed'
    assert 0 <= result['confidence']['score'] <= 1
    assert result['metrics'][0]['formatted_value'].startswith('$')
    assert 'source hashes' not in result['narrative'] and 'SQL' not in result['narrative']
    assert result['metrics'][0]['formatted_value'] in result['narrative']
    assert ('Added 1 Revenue value' if 'quarter' in question else 'Added 4 Revenue values') in result['narrative']
    if 'quarter' in question:
        evidence=system[0].get(f'/api/v1/analyses/{aid}/evidence').json()
        assert len(evidence['plan']['filters'])==2
        assert any('2025' in a['text'] for a in result['assumptions'])


def test_vague_questions_have_lower_interpretation_confidence(system):
    did=financial_data(system)
    results=[]
    for question in ['what is the total revenue','Revenue?']:
        aid=analysis(system,did,question)
        results.append(system[0].get(f'/api/v1/analyses/{aid}/result').json())
    assert results[1]['confidence']['score'] < results[0]['confidence']['score']
    assert results[1]['confidence']['band']=='medium'
    assert any('unspecified' in a['text'] for a in results[1]['assumptions'])


def test_latest_quarter_uses_calendar_window_not_single_latest_row(system):
    did=dataset(system,[("files",("sales.csv",b'Quarter,Revenue\n2025-06-30,"$1,000"\n2025-07-01,"$2,000"\n2025-09-30,"$3,000"\n'))])
    aid=analysis(system,did,'what is the revenue made in the last quarter')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['metrics'][0]['value']==5000
    assert 'Added 2 Revenue values' in result['narrative']
    assert '$2,000 + $3,000 = $5,000' in result['narrative']
    assert '2025-07-01' in result['narrative'] and '2025-10-01' in result['narrative']


def test_average_explains_division_and_missing_values(system):
    did=dataset(system,[("files",('numbers.csv',b'amount\n10\n20\n\n30\n'))])
    aid=analysis(system,did,'mean amount')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['metrics'][0]['value']==20
    assert '60' in result['narrative'] and '÷ 3 = 20' in result['narrative']


@pytest.mark.parametrize('values',[['$1,00','$2,000'],['$1,000','€2,000'],['$1,000 USD'],['$1,000',1000],['$0.000000000000000001234567890123456789'],['$9007199254740993']])
def test_ambiguous_or_precision_losing_currency_formats_are_not_coerced(values):
    assert currency_numbers(values) is None


def test_currency_negative_parentheses_are_lossless():
    assert currency_numbers(['($1,000.50)','$20.50',None])==([-1000.5,20.5,None],'$')


def test_ambiguous_financial_field_is_still_clarified(system):
    did=dataset(system,[("files",('sales.csv',b'Quarter,GrossSales,NetSales\n2025-09-30,"$1,000","$800"\n'))])
    aid=analysis(system,did,'Revenue?')
    state=system[0].get(f'/api/v1/analyses/{aid}').json()
    assert state['status']=='needs_clarification'


def test_disabled_assumptions_require_total_and_quarter_confirmation(system):
    did=financial_data(system); client,worker,*_=system
    response=client.post('/api/v1/analyses',json={'dataset_id':did,'question':'what is the revenue made in the last quarter','preferences':{'allow_explicit_assumptions':False}})
    aid=response.json()['id']
    for _ in range(4):
        worker.run_once()
        state=client.get(f'/api/v1/analyses/{aid}').json()
        if state['status']!='needs_clarification':break
        c=state['clarification']
        client.post(f'/api/v1/analyses/{aid}/clarifications',json={'clarification_id':c['id'],'choice_id':c['choices'][0]['id']})
    assert state['status']=='completed',state
    result=client.get(f'/api/v1/analyses/{aid}/result').json()
    assert result['metrics'][0]['value']==4000


def test_timezone_in_nonlatest_reporting_row_is_not_silently_ignored(system):
    did=dataset(system,[("files",('sales.csv',b'Quarter,Revenue\n2025-07-01T00:00:00+01:00,"$1,000"\n2025-09-30T00:00:00,"$2,000"\n'))])
    aid=analysis(system,did,'what is the revenue made in the last quarter')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='refused' and 'timezone' in result['narrative']
