from backend.streamlit_runtime import EmbeddedBackend


def test_cloud_runtime_upload_verification_and_honest_execution_flags():
    runtime=EmbeddedBackend()
    try:
        response=runtime.request('POST','/datasets',files=[('files',('data.csv',b'Revenue\n10\n20\n','text/csv'))])
        assert response.status_code==202
        dataset=response.json()['id']
        assert runtime.request('GET','/datasets/'+dataset).json()['status']=='ready'
        response=runtime.request('POST','/analyses',json={'dataset_id':dataset,'question':'total revenue'})
        assert response.status_code==202
        analysis=response.json()['id']
        result=runtime.request('GET',f'/analyses/{analysis}/result').json()
        assert result['outcome']=='answered'
        assert result['metrics'][0]['value']==30
        assert result['review']['two_analyst']['status']=='passed'
        evidence=runtime.request('GET',f'/analyses/{analysis}/evidence').json()
        assert evidence['execution']['network_enabled'] is True
        assert evidence['execution']['inputs_read_only'] is False
        assert any(c['id']=='audit_python' for c in evidence['code_artifacts'])
    finally:
        runtime.close()


def test_streamlit_cloud_result_view_without_external_backend(monkeypatch):
    import pytest
    pytest.importorskip('streamlit')
    from pathlib import Path
    from unittest.mock import patch
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv('SURECOUNT_API_URL',raising=False)
    runtime=EmbeddedBackend()
    try:
        dataset=runtime.request('POST','/datasets',files=[('files',('test.csv',b'Revenue\n10\n20\n','text/csv'))]).json()['id']
        analysis=runtime.request('POST','/analyses',json={'dataset_id':dataset,'question':'total revenue'}).json()['id']
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'))
        app.session_state['backend']=''
        app.session_state['dataset_id']=dataset
        app.session_state['analysis_id']=analysis
        with patch('backend.streamlit_runtime.EmbeddedBackend',return_value=runtime):
            app.run(timeout=30)
        assert not app.exception,list(app.exception)
        assert any('30' in m.value for m in app.metric)
        assert app.expander[0].proto.expanded is False
        assert any('connection.execute' in c.value for c in app.code)
    finally:
        runtime.close()
