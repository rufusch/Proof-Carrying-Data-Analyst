"""Allowlisted query compiler and independent Decimal-based verification."""
import math
import re
from datetime import date, datetime
from collections import defaultdict
from decimal import Decimal, localcontext

import duckdb
import pandas as pd

from .common import canonical, digest
from .planner import Query, Aggregate


class UnsafePlan(ValueError):
    pass


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (float, int)):
        if not math.isfinite(value):
            raise UnsafePlan("Non-finite filter value.")
        return str(value)
    return "'" + value.replace("'", "''") + "'"


def prepare(query, profile, tables):
    q = Query.model_validate(query)
    by_id = {t["id"]: t for t in profile["tables"]}
    if q.table not in by_id:
        raise UnsafePlan("The selected table does not exist.")
    columns = {c["name"]: c for c in by_id[q.table]["columns"]}
    expressions = {name: f'b.{quote(name)}' for name in columns}
    if q.join:
        if q.join.table == q.table or q.join.table not in by_id:
            raise UnsafePlan("Invalid join table.")
        right = {c["name"]: c for c in by_id[q.join.table]["columns"]}
        if q.join.left_column not in columns or q.join.right_column not in right:
            raise UnsafePlan("Join columns do not exist.")
        if columns[q.join.left_column]["inferred_type"] != right[q.join.right_column]["inferred_type"]:
            raise UnsafePlan("Join key types differ.")
        if not right[q.join.right_column]["candidate_key"]:
            raise UnsafePlan("Join requires a unique, non-null right key to prevent aggregate fan-out.")
        if tables is not None:
            index = {canonical(r[q.join.right_column]): r for r in tables[q.join.table]}
            if len(index) != len(tables[q.join.table]):
                raise UnsafePlan("Join would multiply rows.")
            if any(r[q.join.left_column] is None or canonical(r[q.join.left_column]) not in index for r in tables[q.table]):
                raise UnsafePlan("Join would omit unmatched or null keys.")
        for name, column in right.items():
            key = q.join.table + "." + name
            if key in columns:
                raise UnsafePlan("Joined column alias collision.")
            columns[key] = column
            expressions[key] = f'r.{quote(name)}'
    required = [x for x in [q.column, q.group_by, q.denominator] if x is not None] + [f.column for f in q.filters]
    required += [a.column for a in q.additional_aggregates if a.column is not None]
    if q.period:
        required.append(q.period.column)
    if any(x not in columns for x in required):
        raise UnsafePlan("A requested column does not exist.")
    if q.operation != "count" and (q.column is None or columns[q.column]["inferred_type"] not in {"number", "integer"}):
        kind = columns.get(q.column, {}).get("inferred_type", "missing")
        raise UnsafePlan(f'This aggregate requires a numeric column with resolved units. "{q.column}" is stored as {kind}. Currency symbols, comma-formatted money and mixed text are preserved as text. Convert a copy to plain numeric values with explicit units, or choose a numeric column.')
    advanced = q.operation in {"ratio", "percentage", "growth", "difference"}
    if q.conversion:
        if q.operation != 'sum' or q.additional_aggregates or q.join or q.period:
            raise UnsafePlan('Conversion supports a single, optionally filtered/grouped total without joins or period comparisons.')
        sources={w['code'].removeprefix('CURRENCY_FORMAT_') for w in profile['warnings'] if w.get('table_id')==q.table and w.get('column')==q.column}
        marker=re.search(r'\b(USD|EUR|GBP|INR)\b',q.column or '',re.I)
        if marker:sources.add(marker[1].upper())
        if q.conversion.source not in sources:
            raise UnsafePlan('The supplied conversion source currency does not match the source field.')
    if advanced and q.additional_aggregates:
        raise UnsafePlan("Advanced calculations cannot be combined with additional aggregates in one query.")
    for aggregate in q.additional_aggregates:
        if aggregate.operation != "count" and (aggregate.column is None or columns[aggregate.column]["inferred_type"] not in {"number", "integer"}):
            raise UnsafePlan("Each additional aggregate requires a numeric column.")
    if q.operation in {"ratio", "percentage"} or (q.operation == "difference" and q.period is None):
        if q.denominator is None or columns[q.denominator]["inferred_type"] not in {"number", "integer"}:
            raise UnsafePlan("This comparison requires a second numeric column.")
        if q.operation == "difference":
            currencies = {}
            for w in profile.get("warnings", []):
                if w['code'].startswith('CURRENCY_FORMAT_'):
                    key = w['column'] if w['table_id'] == q.table else w['table_id'] + '.' + w['column']
                    currencies[key] = w['code']
            if currencies.get(q.column) != currencies.get(q.denominator):
                raise UnsafePlan("The two fields do not have matching currency units. Convert them to a common, explicit unit before calculating their difference.")
    elif q.denominator is not None:
        raise UnsafePlan("A denominator is only valid for ratio and percentage calculations.")
    if q.operation == "growth" or (q.operation == "difference" and q.period is not None):
        if q.period is None:
            raise UnsafePlan("Growth requires explicit baseline and current calendar periods.")
        if columns[q.period.column]["inferred_type"] not in ({"integer", "number"} if q.period.kind == "year" else {"date", "datetime"}):
            raise UnsafePlan("Growth requires an unambiguous ISO calendar date column.")
        boundaries = [q.period.baseline_start, q.period.baseline_end, q.period.current_start, q.period.current_end]
        if q.period.kind == 'year' and any(not value.endswith('-01-01') for value in boundaries):
            raise UnsafePlan('Numeric reporting years require whole-year comparison periods.')
        try:
            if any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in boundaries):
                raise ValueError()
            days = [date.fromisoformat(value) for value in boundaries]
        except ValueError:
            raise UnsafePlan("Period boundaries must be valid ISO dates (YYYY-MM-DD).")
        if not days[0] < days[1] <= days[2] < days[3]:
            raise UnsafePlan("Baseline and current periods must be ordered and non-overlapping.")
    elif q.period is not None:
        raise UnsafePlan("Period comparisons are only valid for growth calculations.")
    for f in q.filters:
        kind = columns[f.column]["inferred_type"]
        if f.operator not in {"is_null", "not_null"}:
            if f.value is None:
                raise UnsafePlan("Use is_null or not_null for missing-value filters.")
            if kind in {"integer", "number"} and (isinstance(f.value, bool) or not isinstance(f.value, (float, int))):
                raise UnsafePlan("Numeric filters need numeric values.")
            if kind in {"string", "date", "datetime"} and not isinstance(f.value, str):
                raise UnsafePlan("Text/date filters need textual values.")
            if kind == "boolean" and not isinstance(f.value, bool):
                raise UnsafePlan("Boolean filters need true or false.")
    group = expressions[q.group_by] if q.group_by is not None else None
    def aggregate_sql(operation, column):
        arg = expressions[column] if column is not None else "*"
        if q.conversion and column==q.column:
            arg=f'({arg} * {literal(q.conversion.rate)})'
        op = {"mean": "AVG"}.get(operation, operation.upper())
        return f"{op}({arg})"
    period_conditions = []
    if q.operation == "growth" or (q.operation == "difference" and q.period is not None):
        for start, end in [(q.period.baseline_start, q.period.baseline_end), (q.period.current_start, q.period.current_end)]:
            expr = expressions[q.period.column] if q.period.kind == "year" else f"CAST({expressions[q.period.column]} AS DATE)"
            period_conditions.append(f"({expr} >= {int(start[:4])} AND {expr} < {int(end[:4])})" if q.period.kind == "year" else f"({expr} >= DATE {literal(start)} AND {expr} < DATE {literal(end)})")
        calculations = [f"SUM(CASE WHEN {condition} THEN {expressions[q.column]} END) AS {key}" for condition, key in zip(period_conditions, ["baseline", "current"])]
    elif q.operation in {"ratio", "percentage"} or (q.operation == "difference" and q.period is None):
        calculations = [f"SUM({expressions[q.column]}) AS numerator", f"SUM({expressions[q.denominator]}) AS denominator"]
    else:
        calculations = [aggregate_sql(q.operation, q.column) + " AS value"]
        calculations += [aggregate_sql(a.operation, a.column) + f" AS metric_{i + 2}" for i, a in enumerate(q.additional_aggregates)]
    sql = f'SELECT {group + " AS group_value, " if group else ""}{", ".join(calculations)} FROM {quote(q.table)} AS b'
    if q.join:
        sql += f' INNER JOIN {quote(q.join.table)} AS r ON b.{quote(q.join.left_column)} = r.{quote(q.join.right_column)}'
    comparisons = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
    clauses = []
    for f in q.filters:
        col = expressions[f.column]
        clauses.append(col + (" IS NULL" if f.operator == "is_null" else " IS NOT NULL" if f.operator == "not_null" else " " + comparisons[f.operator] + " " + literal(f.value)))
    if period_conditions:
        clauses.append("(" + " OR ".join(period_conditions) + ")")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    if group:
        sql += " GROUP BY " + group
    if q.operation == "growth" or (q.operation == "difference" and q.period is not None):
        value_sql = "current - baseline" if q.operation == "difference" else "100.0 * ((current - baseline) / NULLIF(baseline, 0))"
        sql = f"SELECT *, current - baseline AS change, {value_sql} AS value FROM ({sql}) AS aggregate_values"
    elif q.operation in {"ratio", "percentage"} or (q.operation == "difference" and q.period is None):
        scale = "100.0 * " if q.operation == "percentage" else ""
        value_sql = "numerator - denominator" if q.operation == "difference" else f"{scale}(numerator / NULLIF(denominator, 0))"
        sql = f"SELECT *, {value_sql} AS value FROM ({sql}) AS aggregate_values"
    # 501 detects overflow; a truncated aggregate is never silently published.
    sql += " LIMIT 501"
    return q, sql, columns


def normalize(rows):
    for row in rows:
        for key, value in row.items():
            if isinstance(value, Decimal):
                if abs(value) > 2**53:
                    raise UnsafePlan("Result exceeds the exactly representable JSON numeric range.")
                row[key] = float(value)
                if value and row[key] == 0:
                    raise UnsafePlan("Result underflows the supported JSON numeric range.")
            if isinstance(row[key], float) and not math.isfinite(row[key]):
                raise UnsafePlan("A calculation produced a non-finite result.")
            if isinstance(row[key], (float, int)) and not isinstance(row[key], bool) and abs(row[key]) > 2**53:
                raise UnsafePlan("Result exceeds the exactly representable JSON numeric range.")
    return sorted(rows, key=lambda r: canonical(r.get("group_value")))


def sql_execute(sql, profile, tables):
    with duckdb.connect(config={"enable_external_access": "false", "allow_unsigned_extensions": "false", "threads": "1", "memory_limit": "256MB"}) as db:
        for table in profile["tables"]:
            frame = pd.DataFrame(tables[table["id"]], columns=[c["name"] for c in table["columns"]], dtype=object)
            db.register(table["id"], frame)
        cursor = db.execute(sql)
        names = [d[0] for d in cursor.description]
        return normalize([dict(zip(names, row)) for row in cursor.fetchall()])


def selected_rows(q, tables):
    """Apply typed joins/filters in Python; no generated SQL is evaluated here."""
    rows = tables[q.table]
    if q.join:
        index = {canonical(r[q.join.right_column]): r for r in tables[q.join.table]}
        rows = [{**r, **{q.join.table + "." + k: v for k, v in index[canonical(r[q.join.left_column])].items()}} for r in rows]
    def keep(row):
        for f in q.filters:
            value = row[f.column]
            if f.operator == "is_null":
                good = value is None
            elif f.operator == "not_null":
                good = value is not None
            elif value is None:
                good = False
            elif f.operator == "eq":
                good = value == f.value
            elif f.operator == "ne":
                good = value != f.value
            elif f.operator == "gt":
                good = value > f.value
            elif f.operator == "ge":
                good = value >= f.value
            elif f.operator == "lt":
                good = value < f.value
            else:
                good = value <= f.value
            if not good:
                return False
        return True
    return [row for row in rows if keep(row)]


def calendar_day(value):
    if not isinstance(value, str):
        raise UnsafePlan("Period dates contain null or non-date values; filter or resolve them first.")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is not None or timestamp.time().isoformat() != "00:00:00":
            raise ValueError()
        return timestamp.date().isoformat()
    except ValueError:
        raise UnsafePlan("Period dates must be valid ISO dates or naive midnight timestamps; resolve timezone/time-of-day ambiguity first.")


def period_day(value, kind):
    if kind == "year":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value) or not 1 <= value <= 9999:
            raise UnsafePlan("Reporting years must be whole, valid calendar years; resolve missing or fractional years first.")
        return f"{int(value):04d}-01-01"
    return calendar_day(value)


def decimal_aggregate(operation, column, rows):
    values = [row[column] if column is not None else 1 for row in rows]
    present = [value for value in values if value is not None]
    if operation == "count":
        return len(present)
    if not present:
        return None
    numbers = [Decimal(str(value)) for value in present]
    if operation in {"sum", "mean"}:
        total = sum(numbers, Decimal(0))
        return total / len(numbers) if operation == "mean" else total
    return min(numbers) if operation == "min" else max(numbers)


def independent(q, tables):
    """No SQL, pandas aggregation, or generated-code reuse in this calculation."""
    with localcontext() as context:
        # Enough precision for finite binary64 decimal exponents and bounded row counts.
        context.prec = 768
        return independent_decimal(q, tables)


def independent_decimal(q, tables):
    groups = defaultdict(list)
    if q.group_by is None:
        groups[canonical(None)] = []
    labels = {}
    for row in selected_rows(q, tables):
        if q.period:
            day = period_day(row[q.period.column], q.period.kind)
            if not (q.period.baseline_start <= day < q.period.baseline_end or q.period.current_start <= day < q.period.current_end):
                continue
        label = row[q.group_by] if q.group_by is not None else None
        key = canonical(label)
        labels[key] = label
        groups[key].append(row)
        if len(groups) > 500:
            raise UnsafePlan("More than 500 groups; narrow the question with filters.")
    result = []
    for key, rows in groups.items():
        values = {}
        if q.operation == "growth" or (q.operation == "difference" and q.period is not None):
            baseline = [r for r in rows if q.period.baseline_start <= period_day(r[q.period.column], q.period.kind) < q.period.baseline_end]
            current = [r for r in rows if q.period.current_start <= period_day(r[q.period.column], q.period.kind) < q.period.current_end]
            previous = decimal_aggregate("sum", q.column, baseline)
            latest = decimal_aggregate("sum", q.column, current)
            if previous is None or latest is None:
                raise UnsafePlan("Every reported group needs non-null amounts in both comparison periods; missing periods are not treated as zero.")
            if q.operation == "growth" and previous <= 0:
                raise UnsafePlan("Percentage growth requires a strictly positive baseline in every group.")
            if any(r[q.column] is None for r in baseline + current):
                raise UnsafePlan("Growth amounts contain nulls in a comparison period; resolve them before comparing totals.")
            values = {"baseline": previous, "current": latest, "change": latest - previous, "value": (latest - previous) / previous * 100 if q.operation == "growth" else latest - previous}
        elif q.operation in {"ratio", "percentage"} or (q.operation == "difference" and q.period is None):
            if any(r[q.column] is None or r[q.denominator] is None for r in rows):
                raise UnsafePlan("Ratio inputs contain nulls; numerator and denominator must describe the same complete rows.")
            numerator = decimal_aggregate("sum", q.column, rows)
            denominator = decimal_aggregate("sum", q.denominator, rows)
            if numerator is None or denominator is None or (q.operation != "difference" and denominator == 0):
                raise UnsafePlan("Ratio requires nonempty inputs and a nonzero denominator in every group.")
            values = {"numerator": numerator, "denominator": denominator, "value": numerator - denominator if q.operation == "difference" else numerator / denominator * (100 if q.operation == "percentage" else 1)}
        else:
            values["value"] = decimal_aggregate(q.operation, q.column, rows)
            values.update({f"metric_{i + 2}": decimal_aggregate(a.operation, a.column, rows) for i, a in enumerate(q.additional_aggregates)})
        if q.conversion and values['value'] is not None:
            values['value'] *= Decimal(str(q.conversion.rate))
        result.append({**({"group_value": labels[key]} if q.group_by is not None else {}), **values})
    return normalize(result)


def equivalent(left, right):
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if canonical(a.get("group_value")) != canonical(b.get("group_value")):
            return False
        if a.keys() != b.keys():
            return False
        for key in a.keys() - {"group_value"}:
            x, y = a[key], b[key]
            if x is None or y is None:
                if x is not y:
                    return False
            elif not math.isclose(x, y, rel_tol=1e-10, abs_tol=1e-9):
                return False
    return True


def execute(query, profile, tables):
    q, sql, columns = prepare(query, profile, tables)
    calendar_fields = {f.column for f in q.filters if columns[f.column]["inferred_type"] in {"date", "datetime"}
        and f.operator in {"ge", "gt", "lt", "le"} and isinstance(f.value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.value)}
    if calendar_fields:
        for row in selected_rows(q.model_copy(update={"filters": []}), tables):
            for name in calendar_fields:
                if row[name] is not None:
                    calendar_day(row[name])
    # Validate denominator/period semantics before DuckDB casts or divides anything.
    expected = independent(q, tables)
    actual = sql_execute(sql, profile, tables)
    if len(actual) > 500:
        raise UnsafePlan("More than 500 groups; narrow the question with filters.")
    details = []
    if q.group_by is None and q.operation in {"sum", "mean", "min", "max", "count"}:
        selected = selected_rows(q, tables)
        with localcontext() as context:
            context.prec = 768
            for aggregate in [Aggregate(operation=q.operation, column=q.column)] + q.additional_aggregates:
                values = [r[aggregate.column] for r in selected if r[aggregate.column] is not None] if aggregate.column else [1 for r in selected]
                details.append({"count": len(values), "row_count": len(selected), "sample": values[:4],
                    "total": str(sum((Decimal(str(v)) for v in values), Decimal(0))) if aggregate.operation == "mean" else None})
    from .review import assess, semantics_hash
    return {"rows": actual, "sql": sql, "independent_passed": equivalent(actual, expected), "output_hash": digest(actual), "calculation_details": details,
        "semantics_hash":semantics_hash(q,profile),"review":assess(q,profile,tables,actual),
        "conversion_source_rows":independent(q.model_copy(update={'conversion':None}),tables) if q.conversion else None}
