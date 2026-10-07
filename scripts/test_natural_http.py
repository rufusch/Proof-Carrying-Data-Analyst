"""Real API / Docker acceptance checks for multiple CSV files and broader questions."""
import json
from pathlib import Path
import sys
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.contract import validate


def main():
    report = {"checks": [], "analyses": []}
    with httpx.Client(base_url="http://127.0.0.1:8010/api/v1", timeout=30) as client:
        def checked(response, schema, status=200):
            assert response.status_code == status, response.text
            return validate(schema, response.json())
        def wait(path, schema, terminal):
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                data = checked(client.get(path), schema)
                if data["status"] in terminal:
                    return data
                time.sleep(.5)
            raise RuntimeError("Job timed out")
        checked(client.get('/ready'), 'Readiness')
        files = [("orders.csv", b"Order_ID,OrderAmount,SalesTerritory\nA,100,West\nB,50,East\nC,25,West\n"),
                 ("costs.csv", b"ExpenseTotal,Department\n20,Sales\n30,IT\n"),
                 ("alternate-sales.csv", b"GrossSales,NetSales\n999,888\n")]
        created = checked(client.post('/datasets', files=[('files', (name, raw, 'text/csv')) for name, raw in files]), 'Dataset', 202)
        did = created['id']
        assert wait('/datasets/'+did, 'Dataset', {'ready', 'failed'})['status'] == 'ready'
        profile = checked(client.get('/datasets/'+did+'/profile'), 'DatasetProfile')
        assert len(profile['tables']) == 3
        report['checks'].append('Three different CSV files ingested in real isolated containers')
        for question, expected, table_name, field_name in [
            ('How much revenue did we make?', 175, 'orders', None),
            ('What is our average sales?', 175/3, 'orders', None),
            ('How many orders do we have?', 3, None, None),
            ('Could you show me total costs?', 50, None, None),
            ('How much revenue did we make?', 888, 'alternate-sales', 'NetSales'),
        ]:
            started = checked(client.post('/analyses', json={'dataset_id': did, 'question': question}), 'Analysis', 202)
            aid = started['id']
            for _ in range(5):
                state = wait('/analyses/'+aid, 'Analysis', {'needs_clarification', 'completed', 'refused', 'failed'})
                if state['status'] != 'needs_clarification':
                    break
                clarification = state['clarification']
                desired = table_name if clarification['question'] == 'Which table should be analyzed?' else field_name
                choice = next(c for c in clarification['choices'] if c['label'] == desired)
                checked(client.post('/analyses/'+aid+'/clarifications', json={'clarification_id': clarification['id'], 'choice_id': choice['id']}), 'Analysis', 202)
            assert state['status'] == 'completed', state
            result = checked(client.get('/analyses/'+aid+'/result'), 'AnalysisResult')
            evidence = checked(client.get('/analyses/'+aid+'/evidence'), 'EvidencePackage')
            assert abs(result['metrics'][0]['value'] - expected) < 1e-9, result
            assert result['verification']['status'] == 'passed' and result['verification']['reproducible']
            assert not evidence['execution']['network_enabled'] and evidence['execution']['inputs_read_only']
            report['analyses'].append({'question': question, 'result': result, 'evidence': evidence})
            report['checks'].append(question + ' -> ' + str(expected))
    report['status'] = 'passed'
    target = ROOT / '.test-env/natural-language-report.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'passed', 'checks': report['checks'], 'report': str(target)}, indent=2))


if __name__ == '__main__':
    main()
