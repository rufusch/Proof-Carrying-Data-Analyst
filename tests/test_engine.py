from copy import deepcopy
import pytest

from backend.engine import execute, prepare, UnsafePlan
from backend.common import uid
from backend.ingest import ingest, IngestError


def data(tmp_path, raw=b"category,amount\nA,0.1\nA,0.2\nB,10\nB,\n"):
    from backend.common import digest
    file = {"id": uid(), "filename": "input.csv", "sha256": digest(raw)}
    (tmp_path / file["id"]).write_bytes(raw)
    result = ingest(tmp_path, {"id": uid(), "files": [file]}, {"max_rows": 1000, "max_columns": 200, "max_tables": 10})
    return result["profile"], result["tables"]


def query(profile, **overrides):
    return {"table": profile["tables"][0]["id"], "operation": "sum", "column": "amount", "group_by": None, "filters": [], "join": None, **overrides}


@pytest.mark.parametrize("op,expected", [("sum", 10.3), ("mean", 10.3 / 3), ("min", .1), ("max", 10), ("count", 3)])
def test_aggregates_with_nulls_and_decimal_verifier(tmp_path, op, expected):
    profile, tables = data(tmp_path)
    result = execute(query(profile, operation=op), profile, tables)
    assert result["independent_passed"]
    assert result["rows"][0]["value"] == pytest.approx(expected)


@pytest.mark.parametrize("op,value,expected", [("eq", "A", .3), ("ne", "A", 10), ("is_null", None, None), ("not_null", None, 10.3)])
def test_filters(tmp_path, op, value, expected):
    profile, tables = data(tmp_path)
    result = execute(query(profile, filters=[{"column": "category", "operator": op, "value": value}]), profile, tables)
    assert result["independent_passed"]
    actual = result["rows"][0]["value"]
    assert actual is None if expected is None else actual == pytest.approx(expected)


def test_column_and_literal_injection_are_quoted(tmp_path):
    profile, tables = data(tmp_path)
    with pytest.raises(UnsafePlan):
        prepare(query(profile, column="amount); COPY data TO '/tmp/x'"), profile, tables)
    result = execute(query(profile, filters=[{"column": "category", "operator": "eq", "value": "A' OR TRUE --"}]), profile, tables)
    assert result["rows"] == [{"value": None}]
    assert result["independent_passed"]


def test_count_rows_differs_from_count_values(tmp_path):
    profile, tables = data(tmp_path)
    result = execute(query(profile, operation="count", column=None), profile, tables)
    assert result["rows"] == [{"value": 4}]
    assert result["independent_passed"]


def test_leading_zero_and_large_identifiers_preserved(tmp_path):
    profile, tables = data(tmp_path, b"id,large,amount\n001,9007199254740993,1\n002,,2\n")
    rows = next(iter(tables.values()))
    assert rows[0]["id"] == "001" and rows[0]["large"] == "9007199254740993"
    assert rows[1]["large"] is None


def test_null_group_and_empty_input(tmp_path):
    profile, tables = data(tmp_path, b"category,amount\n,1\nA,2\n")
    result = execute(query(profile, group_by="category"), profile, tables)
    assert result["independent_passed"] and len(result["rows"]) == 2
    profile, tables = data(tmp_path, b"category,amount\n")
    result = execute(query(profile, operation="count", column=None), profile, tables)
    assert result["rows"] == [{"value": 0}]


def test_nullable_integer_groups_keep_their_type(tmp_path):
    profile, tables = data(tmp_path, b"category,amount\n1,2\n,3\n2,4\n")
    result = execute(query(profile, group_by="category"), profile, tables)
    assert result["independent_passed"]
    assert any(row == {"group_value": 1, "value": 2} for row in result["rows"])


def test_numeric_overflow_is_refused_before_json_rounding(tmp_path):
    profile, tables = data(tmp_path, b"category,amount\nA,9007199254740992.0\nB,1.0\n")
    with pytest.raises(UnsafePlan, match="numeric range"):
        execute(query(profile), profile, tables)


@pytest.mark.parametrize("text", ["0.123456789012345678901", "1e-400"])
def test_precision_loss_at_ingestion_is_not_silent(tmp_path, text):
    profile, tables = data(tmp_path, f"category,amount\nA,{text}\n".encode())
    assert next(iter(tables.values()))[0]["amount"] == text
    assert any(w["code"] == "NUMERIC_TEXT" for w in profile["warnings"])
    with pytest.raises(UnsafePlan, match="numeric column"):
        execute(query(profile), profile, tables)


def test_join_fanout_and_unmatched_keys_refused(tmp_path):
    profile, tables = data(tmp_path, b"category,amount\nA,1\nB,2\n")
    right = deepcopy(profile["tables"][0])
    right["id"] = "right_table"
    profile["tables"].append(right)
    tables["right_table"] = [{"category": "A", "amount": 3}, {"category": "B", "amount": 4}]
    q = query(profile, group_by="right_table.category", join={"table": "right_table", "left_column": "category", "right_column": "category"})
    assert execute(q, profile, tables)["independent_passed"]
    tables["right_table"].append({"category": "A", "amount": 4})
    with pytest.raises(UnsafePlan, match="multiply"):
        execute(q, profile, tables)
    tables["right_table"] = [{"category": "A", "amount": 3}]
    with pytest.raises(UnsafePlan, match="unmatched"):
        execute(q, profile, tables)


@pytest.mark.parametrize("raw", [b"a,a\n1,2\n", b"a,b\n1,2,3\n", b"\n", b"a,A\n1,2\n"])
def test_malformed_csv_refused(tmp_path, raw):
    with pytest.raises(IngestError):
        data(tmp_path, raw)


def test_group_limit_is_not_silent_truncation(tmp_path):
    raw = b"category,amount\n" + b"".join(f"{i},1\n".encode() for i in range(501))
    profile, tables = data(tmp_path, raw)
    with pytest.raises(UnsafePlan, match="500 groups"):
        execute(query(profile, group_by="category"), profile, tables)
