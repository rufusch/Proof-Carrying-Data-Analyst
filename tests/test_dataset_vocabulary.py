import pytest
from conftest import dataset, analysis


@pytest.mark.parametrize('question,expected', [
    ('How much did we make in west?',125),
    ('What is the total money made for WEST?',125),
    ('average revenue for East',50),
    ('How many rows from West?',2),
    ('sum revenue where territory is West',125),
    ('sum rev in West',125),
    ('sum revenue for "West"',125),
])
def test_dataset_values_select_records_without_column_names(system,question,expected):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Territory\n100,West\n50,East\n25,West\n'))])
    aid=analysis(system,did,question)
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='answered',result
    assert result['metrics'][0]['value']==expected
    assert 'Territory equals West' in result['narrative'] or 'Territory equals East' in result['narrative']
    assert result['verification']['status']=='passed'


def test_category_index_reads_beyond_preview_rows(system):
    data='Revenue,Product\n'+'\n'.join(f'{i},Product {i}' for i in range(1,21))+'\n'
    did=dataset(system,[('files',('products.csv',data.encode()))])
    aid=analysis(system,did,'total sales for Product 20')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['metrics'][0]['value']==20


def test_unknown_category_never_becomes_unfiltered_total(system):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Region\n100,West\n'))])
    aid=analysis(system,did,'sum revenue for Neverland')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='refused' and not result['metrics']
    assert 'Neverland' in result['narrative']


def test_same_value_in_two_fields_requires_choice(system):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Origin,Destination\n100,West,East\n50,East,West\n'))])
    aid=analysis(system,did,'sum revenue for West')
    client,worker,*_=system
    state=client.get(f'/api/v1/analyses/{aid}').json()
    assert state['status']=='needs_clarification'
    c=state['clarification']
    option=next(o for o in c['choices'] if o['label']=='Destination = West')
    client.post(f'/api/v1/analyses/{aid}/clarifications',json={'clarification_id':c['id'],'choice_id':option['id']})
    worker.run_once()
    result=client.get(f'/api/v1/analyses/{aid}/result').json()
    assert result['metrics'][0]['value']==50


def test_typo_suggestion_requires_confirmation(system):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Region\n100,West\n'))])
    aid=analysis(system,did,'total reveneu')
    client,worker,*_=system
    state=client.get(f'/api/v1/analyses/{aid}').json()
    assert state['status']=='needs_clarification'
    c=state['clarification']
    client.post(f'/api/v1/analyses/{aid}/clarifications',json={'clarification_id':c['id'],'choice_id':c['choices'][0]['id']})
    worker.run_once()
    assert client.get(f'/api/v1/analyses/{aid}/result').json()['metrics'][0]['value']==100


def test_filters_respect_assumption_preferences(system):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Region\n100,West\n'))])
    client,worker,*_=system
    aid=client.post('/api/v1/analyses',json={'dataset_id':did,'question':'sum revenue for West','preferences':{'allow_explicit_assumptions':False}}).json()['id']
    worker.run_once()
    state=client.get(f'/api/v1/analyses/{aid}').json()
    assert state['status']=='needs_clarification'


def test_numeric_condition_uses_semantic_field_name(system):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Region\n100,West\n50,East\n'))])
    aid=analysis(system,did,'total sales where revenue is above 75')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['metrics'][0]['value']==100


def test_old_dataset_builds_vocabulary_from_full_stored_data(system):
    data='Revenue,Product\n'+'\n'.join(f'{i},Product {i}' for i in range(1,21))+'\n'
    did=dataset(system,[('files',('products.csv',data.encode()))])
    client,worker,store,*_=system
    with store.tx() as conn:
        record=store.get(conn,did,'dataset');record.pop('vocabulary')
        store.put(conn,did,'dataset',record)
    aid=analysis(system,did,'total revenue for Product 20')
    assert client.get(f'/api/v1/analyses/{aid}/result').json()['metrics'][0]['value']==20


def test_descriptive_category_suggestion_is_confirmed(system):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Region\n100,West\n50,East\n'))])
    aid=analysis(system,did,'total revenue for western region')
    client,worker,*_=system
    state=client.get(f'/api/v1/analyses/{aid}').json()
    assert state['status']=='needs_clarification'
    c=state['clarification']
    assert c['choices'][0]['label']=='Region = West'
    client.post(f'/api/v1/analyses/{aid}/clarifications',json={'clarification_id':c['id'],'choice_id':c['choices'][0]['id']})
    worker.run_once()
    assert client.get(f'/api/v1/analyses/{aid}/result').json()['metrics'][0]['value']==100


def test_grouping_phrase_is_not_a_category_filter(system):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Region\n100,West\n50,East\n'))])
    aid=analysis(system,did,'How much revenue did we make for each region?')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='answered',result
    assert len(result['tables'][0]['rows'])==2


def test_difference_can_select_dataset_category(system):
    did=dataset(system,[('files',('sales.csv',b'Revenue,Cost,Region\n100,40,West\n50,20,East\n'))])
    aid=analysis(system,did,'difference between sales and expenses for West')
    result=system[0].get(f'/api/v1/analyses/{aid}/result').json()
    assert result['outcome']=='answered',result
    assert result['metrics'][-1]['value']==60
