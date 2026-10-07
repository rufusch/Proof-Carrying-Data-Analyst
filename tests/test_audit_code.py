import io
from contextlib import redirect_stdout

from backend.audit_code import generate
from backend.engine import execute


def test_portable_audit_code_replays_real_query():
    profile={'tables':[{'id':'sales','columns':[{'name':'Amount','inferred_type':'number'}]}], 'warnings':[]}
    tables={'sales':[{'Amount':100},{'Amount':50}]}
    sql='SELECT SUM(CAST("Amount" AS DOUBLE)) AS value FROM "sales"'
    code=generate(sql,profile,tables,{'sales'})
    captured=io.StringIO()
    with redirect_stdout(captured):
        exec(compile(code,'audit_code.py','exec'),{})
    assert '150.0' in captured.getvalue()
    assert 'python -m pip install duckdb pandas' in code
