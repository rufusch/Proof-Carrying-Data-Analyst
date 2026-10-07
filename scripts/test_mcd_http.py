"""Verify the supplied annual workbook through the real local API."""
import csv
from decimal import Decimal
import io
import json
import sys
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.contract import validate

source=Path(sys.argv[1])
content=source.read_bytes()
records=list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
amounts={int(r['Year']):Decimal(r['Total assets ($B)']) for r in records}
difference=amounts[2022]-amounts[2021]
growth=difference/amounts[2021]*100
report=[]
with httpx.Client(base_url='http://127.0.0.1:8010/api/v1',timeout=30) as client:
    def wait(path,states):
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            response=client.get(path);response.raise_for_status();state=response.json()
            if state['status'] in states:return state
            time.sleep(1)
        raise AssertionError('Timed out waiting for calculation')
    response=client.post('/datasets',files=[('files',(source.name,content))]);response.raise_for_status();did=response.json()['id']
    assert wait('/datasets/'+did,{'ready','failed'})['status']=='ready'
    for question,expected in [('What is the difference between Total Assets between 2021 and 2022?',difference),('Rate of growth for Total Assets between 2021 and 2022',growth),('Rate of growth for Total Assets',growth)]:
        response=client.post('/analyses',json={'dataset_id':did,'question':question});response.raise_for_status();aid=response.json()['id']
        state=wait('/analyses/'+aid,{'completed','refused','failed','needs_clarification'})
        if question=='Rate of growth for Total Assets':
            assert state['status']=='needs_clarification',state
            c=state['clarification'];choice=next(o for o in c['choices'] if o['label']=='2021 to 2022')
            response=client.post('/analyses/'+aid+'/clarifications',json={'clarification_id':c['id'],'choice_id':choice['id']});response.raise_for_status()
            state=wait('/analyses/'+aid,{'completed','refused','failed'})
        assert state['status']=='completed',state
        result=validate('AnalysisResult',client.get('/analyses/'+aid+'/result').json())
        metric=next(m for m in result['metrics'] if m['id']=='metric_value')
        assert abs(Decimal(str(metric['value']))-expected)<Decimal('0.000000001')
        assert result['verification']['status']=='passed' and result['verification']['reproducible']
        assert '50.43' in result['narrative'] and '53.6' in result['narrative'] and '−' in result['narrative']
        report.append(result);print(json.dumps({'question':question,'answer':metric['formatted_value'],'verification':result['verification']['status']}),flush=True)
(ROOT/'.test-env/mcd-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('PASS: original McDonalds CSV annual difference, growth rate and missing-period clarification.')
