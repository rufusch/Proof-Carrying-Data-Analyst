import re
from .common import digest, now

CURRENCY_UNITS = {"DOLLAR": "$", "EURO": "€", "POUND": "£", "RUPEE": "₹", "USD": "USD", "EUR": "EUR", "GBP": "GBP", "INR": "INR"}

WEIGHTS = {"ambiguity": .25, "data_quality": .20, "execution": .15, "verification": .25, "reproducibility": .15}


def confidence(scores, explanations):
    score = round(sum(WEIGHTS[k] * scores.get(k, 0) for k in WEIGHTS), 6)
    band = "high" if score >= .85 else "medium" if score >= .65 else "low"
    return {"score": score, "band": band, "version": "confidence-v1", "summary": "Confidence reflects recorded ambiguity, quality and verification checks.",
        "factors": [{"name": k, "score": scores.get(k, 0), "weight": w, "explanation": explanations.get(k, "Not established.")} for k, w in WEIGHTS.items()]}


def empty_result(id, outcome, reason):
    return {"analysis_id": id, "outcome": outcome, "headline": None, "narrative": reason,
        "metrics": [], "tables": [], "charts": [], "assumptions": [], "warnings": [],
        "confidence": confidence({}, {}), "verification": {"status": "not_run", "checks_passed": 0, "checks_failed": 0, "reproducible": False, "output_hash": None}, "evidence_url": None}


def plan_document(q, assumptions):
    summary = f"Calculate {q.operation} of {q.column or 'rows'}"
    calculations = [q.operation + "(" + (q.column or "*") + ")"]
    if q.operation in {"ratio", "percentage"} or (q.operation == "difference" and q.period is None):
        summary = f"{q.operation.capitalize()} of total {q.column} to total {q.denominator}"
        calculations = [f"SUM({q.column}) / SUM({q.denominator})" + (" * 100" if q.operation == "percentage" else "")]
        if q.operation == "difference":
            summary = f"Difference between total {q.column} and total {q.denominator}"
            calculations = [f"SUM({q.column}) - SUM({q.denominator})"]
    elif q.operation == "growth" or (q.operation == "difference" and q.period is not None):
        p = q.period
        summary = f"Growth in total {q.column}: [{p.baseline_start}, {p.baseline_end}) versus [{p.current_start}, {p.current_end})"
        calculations = [f"Baseline SUM({q.column}) on {p.column} in [{p.baseline_start}, {p.baseline_end})",
            f"Current SUM({q.column}) on {p.column} in [{p.current_start}, {p.current_end})", "Change = current - baseline", "Growth percent = (current - baseline) / baseline * 100"]
        if q.operation == "difference":
            summary = f"Change in total {q.column}: [{p.baseline_start}, {p.baseline_end}) versus [{p.current_start}, {p.current_end})"
            calculations = calculations[:3]
    if q.additional_aggregates:
        calculations += [a.operation + "(" + (a.column or "*") + ")" for a in q.additional_aggregates]
        summary = "Calculate " + ", ".join(calculations)
    if q.conversion:
        summary+=f' converted from {q.conversion.source} to {q.conversion.target}'
        calculations=[f'SUM({q.column}) * supplied rate {q.conversion.rate} {q.conversion.target} per {q.conversion.source}']
    return {"summary": summary + (f" by {q.group_by}" if q.group_by else ""),
        "tables": [q.table] + ([q.join.table] if q.join else []),
        "joins": [f"Inner many-to-one join: {q.join.left_column} = {q.join.table}.{q.join.right_column}; no unmatched keys permitted."] if q.join else [],
        "filters": [f"{f.column} {f.operator} {f.value!r}" for f in q.filters],
        "cleaning_steps": ["Original source files retained unchanged. Profile warnings document any lossless currency-format normalization. Missing cells become null. Numeric aggregates exclude nulls; duplicate rows are retained."],
        "calculations": calculations, "outputs": ["grouped calculations" if q.group_by else "scalar calculations"], "assumptions": assumptions}


def output_fields(q):
    """Stable row keys; display labels carry requested column names and units."""
    if q.operation == "growth" or (q.operation == "difference" and q.period is not None):
        return [("baseline", "Baseline total", None), ("current", "Current total", None), ("change", "Absolute change", None), ("value", "Growth" if q.operation == "growth" else "Difference", "percent" if q.operation == "growth" else None)]
    if q.operation in {"ratio", "percentage", "difference"}:
        return [("numerator", f"Total {q.column}", None), ("denominator", f"Total {q.denominator}", None), ("value", q.operation.capitalize(), "percent" if q.operation == "percentage" else "ratio")]
    labels = {"sum": "Total", "mean": "Average", "min": "Lowest", "max": "Highest", "count": "Count of"}
    def label(a):
        return f"{labels[a.operation]} {a.column or 'rows'}"
    return [("value", label(q), None)] + [(f"metric_{i + 2}", label(a), None) for i, a in enumerate(q.additional_aggregates)]


def calculation_explanation(q, output, fields, used, decision):
    names = ", ".join(t.get("sheet_name") or t["name"] for t in used)
    lines = [f"Used {names}."]
    relations = {"eq": "equals", "ne": "does not equal", "ge": "is on or after / at least", "gt": "is after / greater than", "lt": "is before / less than", "le": "is on or before / at most", "is_null": "is empty", "not_null": "is not empty"}
    if q.filters:
        lines.append("Selected records where " + " and ".join(f"{f.column} {relations[f.operator]}" + (f" {f.value}" if f.operator not in {"is_null", "not_null"} else "") for f in q.filters) + ".")
    if decision.interpretation_score is not None and decision.interpretation_score <= .45:
        lines.append(f"Interpreted the unspecified calculation as total {q.column}.")
    if q.group_by:
        lines.append(f"Separated the records by {q.group_by} and calculated {fields[0][1].lower()} for each group. The table shows each group's result.")
        if q.conversion:
            lines.append(f'Each {q.conversion.source} group total × your supplied rate {q.conversion.rate:.12g} = the displayed {q.conversion.target} total.')
        return "\n".join(lines)
    row = output["rows"][0]
    if q.conversion:
        source=output['conversion_source_rows'][0]['value']
        lines.append(f'Added the selected {q.column} values: {display(source,q.conversion.source)}. Applied your supplied rate of {q.conversion.rate:.12g} {q.conversion.target} per {q.conversion.source}.')
        lines.append(f'{display(source,None)} × {q.conversion.rate:.12g} = {display(row["value"],q.conversion.target)}.')
        return '\n'.join(lines)
    if q.operation in {"ratio", "percentage"} or (q.operation == "difference" and q.period is None):
        lines.append(f"Added {q.column} to get {display(row['numerator'], None)} and {q.denominator} to get {display(row['denominator'], None)}.")
        symbol = "−" if q.operation == "difference" else "÷"
        lines.append(f"{display(row['numerator'], fields[0][2])} {symbol} {display(row['denominator'], fields[1][2])}" + (" × 100" if q.operation == "percentage" else "") + f" = {display(row['value'], fields[-1][2])}.")
    elif q.operation == "growth" or (q.operation == "difference" and q.period is not None):
        p = q.period
        lines.append(f"Source column: {q.column}; reporting field: {p.column}.")
        if p.kind == 'year':
            lines.append(f"{p.baseline_start[:4]} total: {display(row['baseline'], fields[0][2])}. {p.current_start[:4]} total: {display(row['current'], fields[1][2])}.")
        else:
            lines.append(f"Baseline period: {p.baseline_start} to before {p.baseline_end}; total {display(row['baseline'], fields[0][2])}. Comparison period: {p.current_start} to before {p.current_end}; total {display(row['current'], fields[1][2])}.")
        lines.append(f"Change = {display(row['current'], fields[1][2])} − {display(row['baseline'], fields[0][2])} = {display(row['change'], fields[2][2])}.")
        if q.operation == "growth":
            lines.append(f"Growth = {display(row['change'], None)} ÷ {display(row['baseline'], None)} × 100 = {display(row['value'], 'percent')}.")
            lines.append("The percentage compares the change with the baseline total. Displayed values are rounded for readability; the calculation uses the full stored precision.")
    else:
        aggregates = [(q.operation, q.column)] + [(a.operation, a.column) for a in q.additional_aggregates]
        for (key, label, unit), (operation, column), detail in zip(fields, aggregates, output.get("calculation_details", [])):
            count, answer = detail['count'], display(row[key], unit)
            subject = column or "rows"
            if operation == "count":
                lines.append(f"Counted {count} {'non-empty ' if column else ''}{subject} entries: {answer}.")
            elif not count:
                lines.append(f"There are no numeric {subject} values in the selected records, so no {label.lower()} can be calculated.")
            elif operation == "sum":
                lines.append(f"Added {count} {subject} value{'s' if count != 1 else ''} to get {answer}.")
                if count <= 4:
                    lines.append(" + ".join(display(v, unit) if v >= 0 else f"({display(v, unit)})" for v in detail['sample']) + f" = {answer}.")
            elif operation == "mean":
                lines.append(f"Added {count} {subject} values, then divided their total by {count}: {detail['total']} ÷ {count} = {answer}.")
            else:
                lines.append(f"Compared {count} {subject} values and selected the {'lowest' if operation == 'min' else 'highest'}: {answer}.")
            missing = detail['row_count'] - count
            if missing:
                lines.append(f"{missing} empty {subject} entries were excluded from this calculation.")
    return "\n".join(lines)


def display(value, unit):
    if value is None:
        return "No non-null values"
    if unit in {'billion dollars','million dollars','thousand dollars'}:
        return f'{value:.12g} {unit}'
    if unit in CURRENCY_UNITS.values():
        amount = format(value, ",.12g") if isinstance(value, float) else format(value, ",")
        return unit + amount if len(unit) == 1 else unit + " " + amount
    return f"{value:.6g}%" if unit == "percent" else f"{value:.12g}" if isinstance(value, float) else str(value)


def package(id, dataset, profile, q, decision, answers, output, rerun, attempts, checks, model_mode):
    rows = output["rows"]
    assumptions = [{"id": f"assumption_{i}", "text": value, "source": "inferred", "impact": "Defines interpretation of the query."} for i, value in enumerate(decision.assumptions)]
    assumptions += [{"id": f"answer_{i}", "text": a["question"] + " " + a["value"], "source": "user", "impact": "Resolves a material ambiguity."} for i, a in enumerate(answers)]
    plan = plan_document(q, assumptions)
    passed = all(c["status"] == "passed" for c in checks)
    sources = []
    files = {f["id"]: f for f in dataset["files"]}
    used = [t for t in profile["tables"] if t["id"] in plan["tables"]]
    for t in used:
        sources.append({"file_id": t["file_id"], "file_sha256": files[t["file_id"]]["sha256"], "table_id": t["id"],
            "columns": [c["name"] for c in t["columns"]], "locator_type": "aggregate_query", "locator": output["sql"]})
    evidence = {"analysis_id": id, "created_at": now(), "plan": plan,
        "code_artifacts": [{"id": "sql_1", "language": "sql", "source": output["sql"], "sha256": digest(output["sql"].encode()), "entry_point": "query.sql", "safety_status": "passed"}],
        "claims": [{"id": "claim_1", "statement": plan["summary"], "value": rows, "source_locators": sources, "code_artifact_id": "sql_1"}] if passed else [],
        "checks": checks, "execution": {"attempt_count": attempts,
            "duration_ms": output["resources"]["duration_ms"] + rerun["resources"]["duration_ms"],
            "cpu_time_ms": output["resources"]["cpu_time_ms"] + rerun["resources"]["cpu_time_ms"],
            "peak_memory_bytes": max(output["resources"]["peak_memory_bytes"], rerun["resources"]["peak_memory_bytes"]),
            "network_enabled": False, "inputs_read_only": True, "output_hash": output["output_hash"]}}
    if output.get("audit_code"):
        code = output["audit_code"]
        evidence["code_artifacts"].append({"id": "audit_python", "language": "python", "source": code, "sha256": digest(code.encode()), "entry_point": "audit_code.py", "safety_status": "passed"})
    result = empty_result(id, "answered" if passed else "refused", "Verification failed; no calculated claims are published.")
    result["evidence_url"] = f"/api/v1/analyses/{id}/evidence"
    result["assumptions"] = assumptions
    result["warnings"] = [w for w in profile["warnings"] if not w.get("table_id") or w.get("table_id") in plan["tables"]]
    result["verification"] = {"status": "passed" if passed else "failed", "checks_passed": sum(c["status"] == "passed" for c in checks),
        "checks_failed": sum(c["status"] == "failed" for c in checks), "reproducible": output["output_hash"] == rerun["output_hash"], "output_hash": output["output_hash"]}
    audit=output.get('review',{})
    independent_ok=output['independent_passed'] and rerun['independent_passed']
    analyst_gate=independent_ok and all(c['status']=='passed' for c in checks if c['type'] in {'mechanical','units_filters'})
    result['review']={'two_analyst':{'status':'passed' if analyst_gate else 'failed','analyst_a':'Agent 1','analyst_b':'Agent 2','tolerance':'relative 1e-10; absolute 1e-9'},
        'skeptic':{'status':audit.get('status','not_run'),'checks':audit.get('checks',[]),'scope':audit.get('scope','')},
        'sensitivity':audit.get('sensitivity',[]),'unknown_impacts':audit.get('unknown_impacts',[]),
        'heatmap':[]}

    if not passed:
        result['narrative']='Verification failed: two independent calculations disagree.' if not independent_ok else 'Verification failed: the skeptic found a material change under an alternative data interpretation.' if audit.get('status')=='failed' else 'Verification failed: units, filters, approved code or repeat checks did not agree.'
        from .recovery import recovery_for
        result['recovery']=recovery_for('',profile,[],result['narrative']+(' Resolve duplicate records before rerunning.' if audit.get('status')=='failed' else ''))
    cells = sum(t["row_count"] * t["column_count"] for t in used)
    nulls = sum(c["null_count"] for t in used for c in t["columns"])
    duplicates = sum(t["duplicate_row_count"] for t in used)
    quality = max(0, 1 - nulls / max(cells, 1) - duplicates / max(sum(t["row_count"] for t in used), 1) - min(.3, .05 * len(result["warnings"])))
    interpretation = decision.interpretation_score if decision.interpretation_score is not None else (.9 if model_mode else 1)
    scores = {"ambiguity": interpretation, "data_quality": quality, "execution": 1, "verification": int(passed), "reproducibility": int(result["verification"]["reproducible"])}
    result["confidence"] = confidence(scores, {"ambiguity": "Typed plan with recorded clarifications; model interpretation remains a limitation." if model_mode else "Exact supported request grammar and explicit table selection.",
        "data_quality": "Penalty for null cells, duplicate rows and warnings in source tables.", "execution": "Allowlisted SQL executed in the isolated worker.",
        "verification": "Independent Decimal recalculation " + ("matched." if passed else "did not establish all checks."), "reproducibility": "Compared canonical output hashes from two fresh containers."})
    if not passed:
        return result, evidence
    if decision.interpretation_score is not None:
        result["confidence"]["factors"][0]["explanation"] = "Interpretation score reflects explicit metric mapping, inferred aggregation and dataset-relative periods. It is a heuristic, not a calibrated probability. Arithmetic is verified separately."
    fields = output_fields(q)
    units = {}
    marker=re.search(r'\b(USD|EUR|GBP|INR)\b',q.column or '',re.I)
    if marker:units[q.column]=marker[1].upper()
    for w in result["warnings"]:
        currency = w["code"].removeprefix("CURRENCY_FORMAT_")
        if currency in CURRENCY_UNITS:
            column = w["column"] if w["table_id"] == q.table else w["table_id"] + "." + w["column"]
            units[column] = CURRENCY_UNITS[currency]
    if q.operation in {"sum", "mean", "min", "max"}:
        aggregates = [(q.operation, q.column)] + [(a.operation, a.column) for a in q.additional_aggregates]
        fields = [(key, label, units.get(column, unit) if operation != "count" else unit) for (key, label, unit), (operation, column) in zip(fields, aggregates)]
    if q.conversion:
        fields=[(key,label,q.conversion.target) for key,label,unit in fields]
    if q.operation == "difference":
        left, right = units.get(q.column), units.get(q.denominator) if q.denominator else units.get(q.column)
        fields = [(key, label, right if key == "denominator" else left if key in {"numerator", "baseline", "current", "change"} or left == right else None) for key, label, unit in fields]
    header_unit = re.search(r'\(\s*(\$[BMK])\s*\)',q.column or '',re.I)
    if header_unit and q.operation in {'growth','difference'} and q.period:
        marker = {'$B':'billion dollars','$M':'million dollars','$K':'thousand dollars'}[header_unit[1].upper()]
        fields = [(key,label,unit if key == 'value' and q.operation == 'growth' else marker) for key,label,unit in fields]
    if q.group_by is None:
        value = rows[0]["value"]
        primary_unit = next(unit for key, _, unit in fields if key == "value")
        formatted = display(value, primary_unit)
        primary_label = next(label for key, label, _ in fields if key == "value")
        result["headline"] = f"{primary_label}: {formatted}."
        result["metrics"] = [{"id": f"metric_{key}", "label": label, "value": rows[0][key], "formatted_value": display(rows[0][key], unit), "unit": unit, "evidence_refs": ["claim_1"]} for key, label, unit in fields]
        if q.additional_aggregates:
            result["headline"] = f"Verified {len(result['metrics'])} calculations."
    else:
        result["headline"] = f"Calculated {len(rows)} groups."
        evidence["claims"].append({"id": "claim_group_count", "statement": "Number of groups in the verified aggregate", "value": len(rows), "source_locators": sources, "code_artifact_id": "sql_1"})
        result["metrics"] = [{"id": "group_count", "label": "Groups returned", "value": len(rows), "formatted_value": str(len(rows)), "unit": "groups", "evidence_refs": ["claim_group_count"]}]
        nonnull = next((r["group_value"] for r in rows if r["group_value"] is not None), "")
        group_type = "boolean" if isinstance(nonnull, bool) else "number" if isinstance(nonnull, (float, int)) else "string"
        columns = [{"key": "group_value", "label": q.group_by, "type": group_type}] + [{"key": key, "label": label + (" (%)" if unit == "percent" else f' ({unit})' if q.conversion else ""), "type": "number"} for key, label, unit in fields]
        result["tables"] = [{"id": "table_1", "title": plan["summary"], "columns": columns, "rows": rows, "total_rows": len(rows), "truncated": False, "evidence_refs": ["claim_1"]}]
        result["charts"] = [{"id": "chart_1", "title": plan["summary"] + (" (%)" if q.operation in {"growth", "percentage"} else ""), "type": "bar", "x_key": "group_value", "y_keys": ["value"], "data": rows, "evidence_refs": ["claim_1"]}]
    result["narrative"] = calculation_explanation(q, output, fields, used, decision)

    return result, evidence
