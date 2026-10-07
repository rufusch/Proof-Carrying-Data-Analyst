import pytest
from conftest import dataset,analysis


def result(system,did,question):
    aid=analysis(system,did,question)
    return aid,system[0].get(f'/api/v1/analyses/{aid}/result').json()


def test_two_analysts_skeptic_and_heatmap(system):
    did=dataset(system)
    _,r=result(system,did,'sum amount')
    assert r['outcome']=='answered'
    assert r['review']['two_analyst']['status']=='passed'
    assert r['review']['skeptic']['status']=='passed'
    assert r['review']['heatmap']==[]
    assert r['review']['two_analyst']['analyst_a']=='Agent 1'
    evidence=system[0].get(r['evidence_url']).json()
    replay=next(c['source'] for c in evidence['code_artifacts'] if c['id']=='audit_python')
    import io
    from contextlib import redirect_stdout
    captured=io.StringIO()
    with redirect_stdout(captured): exec(replay,{})
    assert '35' in captured.getvalue()
    assert len(r['review']['sensitivity'])==1
    assert r['verification']['checks_passed']==5


def test_duplicate_sensitivity_withholds_claims(system):
    did=dataset(system,[('files',('data.csv',b'amount\n100\n100\n50\n'))])
    aid,r=result(system,did,'sum amount')
    assert r['outcome']=='refused' and not r['metrics']
    assert r['review']['skeptic']['status']=='failed'
    changes=r['review']['sensitivity'][0]['impacts']
    issue=next(x for x in changes if x['scenario'].startswith('Remove'))
    assert issue['delta']==-100 and issue['percent_change']==-40
    assert r['recovery']['requirements']
    evidence=system[0].get(f'/api/v1/analyses/{aid}/evidence').json()
    assert not evidence['claims']


def test_alternate_identifier_dedupe_is_challenged(system):
    did=dataset(system,[('files',('data.csv',b'Order_ID,amount\nA,10\nA,20\n'))])
    _,r=result(system,did,'sum amount')
    assert r['outcome']=='refused'
    assert any('Order_ID' in x['name'] and x['status']=='failed' for x in r['review']['skeptic']['checks'])


def test_missing_amount_scenarios_are_assumptions_not_bounds(system):
    did=dataset(system,[('files',('data.csv',b'amount,region\n100,West\n,East\n'))])
    _,r=result(system,did,'sum amount')
    assert r['outcome']=='answered'
    impacts=r['review']['sensitivity'][0]['impacts']
    assert any(i['assumption'] and i['delta']==100 for i in impacts)
    assert any('not guaranteed limits' in m for m in r['review']['unknown_impacts'])


def test_parameterized_fx_refusal_recovers_with_verified_rate(system):
    did=dataset(system,[('files',('data.csv',b'Month,Revenue\nMarch,EUR 100\nMarch,EUR 50\nApril,EUR 900\n'))])
    aid,r=result(system,did,'total revenue for March in USD')
    assert r['outcome']=='refused',r
    assert r['recovery']['source_currency']=='EUR' and r['recovery']['target_currency']=='USD'
    assert 'EUR→USD' in r['narrative']
    scope={};exec(r['recovery']['code_template'],scope)
    assert scope['answer']([100,50],1.1)==165
    client,worker,*_=system
    response=client.post(r['recovery']['resume_url'],json={'rate':1.1,'rate_source':'Test March rate'})
    assert response.status_code==202,response.text
    recovered=response.json()['id'];worker.run_once()
    answer=client.get(f'/api/v1/analyses/{recovered}/result').json()
    assert answer['outcome']=='answered',answer
    assert answer['metrics'][0]['value']==pytest.approx(165)
    assert answer['metrics'][0]['unit']=='USD'
    assert answer['review']['two_analyst']['status']=='passed'
    assert answer['review']['heatmap']==[]
    assert '150' in answer['narrative'] and '1.1' in answer['narrative']


@pytest.mark.parametrize('rate',[0,-1,1000001])
def test_recovery_rejects_invalid_rates(system,rate):
    did=dataset(system,[('files',('data.csv',b'Revenue (EUR)\n100\n'))])
    aid,r=result(system,did,'total revenue in USD')
    assert system[0].post(f'/api/v1/analyses/{aid}/recovery',json={'rate':rate,'rate_source':'Test rate'}).status_code==422


def test_code_and_proposed_values_cannot_be_supplied_by_client(system):
    did=dataset(system,[('files',('data.csv',b'Revenue (EUR)\n100\n'))])
    aid,_=result(system,did,'total revenue in USD')
    assert system[0].post(f'/api/v1/analyses/{aid}/recovery',json={'rate':1.1,'rate_source':'Test rate','code':'print(999)'}).status_code==422


def test_units_and_filters_mismatch_blocks_answer(system):
    did=dataset(system);client,worker,*rest=system;sandbox=system[4];original=sandbox.run
    def mutate(root,payload):
        output=original(root,payload)
        if payload['mode']=='execute':output['semantics_hash']='0'*64
        return output
    sandbox.run=mutate
    _,r=result(system,did,'sum amount')
    assert r['outcome']=='refused' and not r['metrics']
    assert 'units' in r['narrative']


def test_calendar_representation_is_actually_challenged(tmp_path):
    from test_engine import data,query
    from backend.engine import execute
    profile,tables=data(tmp_path,b'amount,day\n10,2025-01-01T00:00:00\n20,2025-02-01T00:00:00\n')
    q=query(profile,operation='growth',period={'column':'day','baseline_start':'2025-01-01','baseline_end':'2025-02-01','current_start':'2025-02-01','current_end':'2025-03-01'})
    output=execute(q,profile,tables)
    assert any('equivalent ISO' in c['name'] and c['status']=='passed' for c in output['review']['checks'])


def test_alternative_safe_join_can_break_candidate(system):
    import json
    from backend.engine import execute
    did=dataset(system,[('files',('left.csv',b'customer_id,alt_id,amount\n1,2,10\n')),('files',('right.csv',b'customer_id,alt_id,price\n1,1,100\n2,2,200\n'))])
    _,_,store,settings,_=system
    with store.tx() as conn:profile=store.get(conn,did,'dataset')['profile']
    tables=json.loads((settings.data_dir/did/'tables.json').read_text())
    left,right=profile['tables'];q={'table':left['id'],'operation':'sum','column':right['id']+'.price','group_by':None,'filters':[],'join':{'table':right['id'],'left_column':'customer_id','right_column':'customer_id'}}
    output=execute(q,profile,tables)
    assert output['independent_passed']
    assert output['review']['status']=='failed'
    assert any('alternative shared identifier' in c['name'] and c['status']=='failed' for c in output['review']['checks'])


def test_group_count_has_sensitivity_component(system):
    did=dataset(system);_,r=result(system,did,'sum amount by region')
    assert any(c['key']=='group_count' for c in r['review']['sensitivity'])
