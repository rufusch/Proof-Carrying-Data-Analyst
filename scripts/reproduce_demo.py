"""Reproduce the screenshot's financial answer using repository datasets."""
import csv
from decimal import Decimal
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.streamlit_runtime import EmbeddedBackend


def main():
    source=ROOT/'datasets/McDonalds_Financial_Statements.csv'
    with source.open(encoding='utf-8-sig',newline='') as file:
        records={int(row['Year']):Decimal(row['Revenue ($B)']) for row in csv.DictReader(file)}
    baseline,current=records[2002],records[2022]
    expected=(current-baseline)/baseline*100
    runtime=EmbeddedBackend()
    try:
        response=runtime.request('POST','/datasets',files=[('files',(source.name,source.read_bytes(),'text/csv'))])
        response.raise_for_status();dataset=response.json()['id']
        response=runtime.request('POST','/analyses',json={'dataset_id':dataset,'question':'What is the growth in revenue between 2002 and 2022?'})
        response.raise_for_status();analysis=response.json()['id']
        for _ in range(5):
            state=runtime.request('GET',f'/analyses/{analysis}').json()
            if state['status']!='needs_clarification':
                break
            clarification=state['clarification']
            choice=next(c for c in clarification['choices'] if c['label']=='Revenue ($B)')
            response=runtime.request('POST',f'/analyses/{analysis}/clarifications',json={'clarification_id':clarification['id'],'choice_id':choice['id']})
            response.raise_for_status()
        response=runtime.request('GET',f'/analyses/{analysis}/result');response.raise_for_status();result=response.json()
        assert result['outcome']=='answered',result['narrative']
        actual=next(m['value'] for m in result['metrics'] if m['id']=='metric_value')
        assert math.isclose(actual,float(expected),rel_tol=1e-10,abs_tol=1e-9),(actual,expected)
        assert result['verification']['status']=='passed'
        assert result['review']['two_analyst']['status']=='passed'
        assert result['review']['skeptic']['status']=='passed'
        print(f'Baseline revenue (2002): {baseline} billion dollars')
        print(f'Comparison revenue (2022): {current} billion dollars')
        print(f'Change: {current-baseline} billion dollars')
        print(f'Growth: {actual:.4f}%')
        print('PASS: source-cell calculation, Agent 1, Agent 2 and extra reviewer agree.')
    finally:
        runtime.close()


if __name__=='__main__':
    main()
