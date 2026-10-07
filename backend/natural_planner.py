"""Conservative schema grounding for the trained local intent model.

Synonyms are transparent, bounded metadata rules, not learned business definitions.
Unsupported residual text never becomes a partial answer.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from decimal import Decimal
import math
from datetime import date, datetime
from .common import digest
from .intent_model import predict

CONCEPTS = [
    {"revenue", "sales", "sale", "turnover", "amount", "income", "earnings", "rev", "receipts", "takings"},
    {"profit", "profits"}, {"cost", "costs", "expense", "expenses", "spend", "spending"},
    {"units", "unit", "quantity", "qty", "quantities"},
    {"region", "regions", "territory", "territories"}, {"country", "countries", "nation", "nations"},
    {"city", "cities", "town", "towns"}, {"customer", "customers", "client", "clients"},
    {"order", "orders", "transaction", "transactions"}, {"product", "products", "item", "items"},
    {"date", "day", "dates", "days"}, {"category", "categories", "type", "types"},
    {"employee", "employees", "staff"}, {"price", "prices"}, {"discount", "discounts"},
    {"temperature", "temp"}, {"rating", "ratings", "score", "scores"},
    {"weight", "mass"}, {"duration", "elapsed"},
]
CANONICAL = {word: sorted(group)[0] for group in CONCEPTS for word in group}
NUMERIC = {"integer", "number"}


def normalize(value):
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = unicodedata.normalize("NFKD", value).casefold()
    text = " ".join(re.findall(r"[a-z0-9]+", value))
    for phrase, replacement in {"money made": "revenue", "money earned": "revenue", "money brought in": "revenue", "money spent": "cost", "sales revenue": "revenue"}.items():
        text = re.sub(r"\b" + phrase + r"\b", replacement, text)
    return text


def tokens(value):
    return {CANONICAL.get(word, word) for word in normalize(value).split() if word not in {"the", "of", "our"}}


def matches(phrase, columns, numeric=False):
    available = [c for c in columns if not numeric or c["inferred_type"] in NUMERIC]
    exact = [c for c in available if normalize(c["name"]) == normalize(phrase)]
    if exact:
        return exact
    desired = tokens(phrase)
    # Qualified terms such as 'net sales' must retain every qualifier.
    conflicting_money = {"profit", "cost", "price", "discount", "tax", "refund", "refunds", "return", "returns", "fee", "fees", "balance", "salary", "payment", "payments"}
    result = []
    for column in available:
        actual = tokens(column["name"])
        if not desired or not desired <= actual:
            continue
        if CANONICAL["revenue"] in desired and (actual & conflicting_money) - desired:
            continue
        result.append(column)
    return result


def choice_id(slot, table, column):
    return "field_" + digest([slot, table, column])[:24]


def request_from_language(question):
    from .planner import advanced_request
    question = question.strip().rstrip("?.!")
    # Never silently drop unsupported clauses or a second request.
    if re.search(r"\b(why|forecast|predict|prediction|tomorrow|caused|causes|causal|explain|execute|select|sql|python)\b", question, re.I):
        return {"error": "Causal explanations, forecasts and code execution are not supported. Ask for a measurable calculation."}
    if re.search(r"\bper\b", question, re.I):
        return {"error": "'Per' can mean grouping or division. Use 'by' for groups, or explicitly request a ratio or average."}
    annual = re.fullmatch(r"(?:(?:what (?:is|was)|show(?: me)?|calculate) (?:the )?)?(difference|change|growth|rate of growth|growth rate|percentage change) (?:between|in|of|for) (.+?) (?:between|from) (\d{4}) (?:and|to) (\d{4})", question, re.I)
    growth_unspecified = re.fullmatch(r"(?:(?:what (?:is|was)|show(?: me)?|calculate) (?:the )?)?(?:rate of growth|growth rate) (?:for|of|in) (.+)", question, re.I)
    if annual:
        operation, metric, baseline, current = annual.groups()
        return {'operation':'difference' if operation.lower() in {'difference','change'} else 'growth','column':metric,'group_by':None,'annual_comparison':[int(baseline),int(current)]}
    if growth_unspecified:
        return {'operation':'growth','column':growth_unspecified[1],'group_by':None,'annual_comparison':[]}
    comparison = re.fullmatch(r"(?:(?:what (?:is|was)|show(?: me)?|calculate) (?:the )?)?(difference|change|growth|percentage change) (?:in|of) (.+?) (?:between (?:the )?(?:last|latest) quarter and (?:the )?previous quarter|from (?:the )?previous quarter to (?:the )?(?:last|latest) quarter)(?: by (.+))?", question, re.I)
    alternate = re.fullmatch(r"(?:(?:what (?:is|was)|show(?: me)?|calculate) (?:the )?)?(difference|change) between (.+?) (?:in|for) (?:the )?(?:last|latest) quarter and (?:the )?previous quarter(?: by (.+))?", question, re.I)
    comparison = comparison or alternate
    if comparison:
        operation, column, group = comparison.groups()
        return {"operation": "growth" if operation.lower() in {"growth", "percentage change"} else "difference", "column": column, "group_by": group, "quarter_comparison": True}
    temporal = re.fullmatch(r"(.+?)\s+(?:(?:in|for|during|over|from)\s+)?(?:the\s+)?(last|latest|most recent|previous) quarter", question, re.I)
    if temporal:
        request = request_from_language(temporal[1])
        if "error" not in request:
            request["quarter_scope"] = "previous" if temporal[2].casefold() == "previous" else "latest"
        return request
    where = re.fullmatch(r"(.+?)\s+where\s+(.+?)\s+(is at least|is at most|is more than|is greater than|is less than|is above|is below|>=|<=|>|<|is|equals|=)\s+(.+)", question, re.I)
    scoped = re.fullmatch(r"(.+?)\s+(?:in|for|from)\s+([^?]+?)(\s+by\s+.+)?", question, re.I)
    # Explicit period comparisons have their own complete grammar below.
    if where or (scoped and not scoped[2].lower().startswith('each ') and not re.search(r"\b(?:subtract|quarter|using|to)\b", question, re.I)):
        body = where[1] if where else scoped[1] + (scoped[3] or '')
        request = request_from_language(body)
        if 'error' not in request:
            operators = {'is at least':'ge','is at most':'le','is more than':'gt','is greater than':'gt','is less than':'lt','is above':'gt','is below':'lt','>=':'ge','<=':'le','>':'gt','<':'lt'}
            request.setdefault('value_filters', []).append({'field': where[2] if where else None, 'phrase': where[4] if where else scoped[2], 'operator':operators.get(where[3].casefold(),'eq') if where else 'eq'})
        return request
    explicit = advanced_request(question)
    if explicit is not None:
        return explicit
    clean = re.sub(r"^(?:please\s+)?(?:(?:can|could|would) you\s+)?", "", question, flags=re.I)
    if re.fullmatch(r"how much (?:money )?did we (?:make|earn|generate)", clean, re.I):
        return {"operation": "sum", "column": "revenue", "group_by": None}
    # Exact aggregate phrases can also use semantic column descriptions.
    exact = re.fullmatch(r"(?:(?:what is|show|calculate) (?:the )?)?(sum|total|average|mean|minimum|min|maximum|max|count) (?:of )?(.+?)(?: by (.+))?", clean, re.I)
    if exact:
        op, target, group = exact.groups()
        op = {"total": "sum", "average": "mean", "minimum": "min", "maximum": "max"}.get(op.lower(), op.lower())
        return {"operation": op, "column": None if op == "count" and target.lower() in {"rows", "records"} else target, "group_by": group}
    patterns = [
        r"(?:what (?:is|are|was|were) (?:our |the )?(?:total|average|mean|minimum|maximum|lowest|highest|smallest|largest|typical)|(?:show(?: me)?|give me|tell me|find|calculate) (?:our |the )?(?:total|average|mean|minimum|maximum|lowest|highest|smallest|largest|typical)|(?:add up|break down (?:the )?(?:total|average))) (?P<metric>.+?)(?: (?:by|per|across|for each|broken down by) (?P<group>.+))?",
        r"(?:how much) (?P<metric>.+?) (?:did we (?:make|generate|earn|record)|do we have|was (?:recorded|generated|earned))(?: (?:by|per|across|for each) (?P<group>.+))?",
        r"(?:how many) (?P<metric>.+?)(?: (?:do we have|are there|were recorded|did we (?:receive|get|process)|have we recorded|exist))?(?: (?:by|per|across|for each) (?P<group>.+))?",
        r"(?:what (?:is|was)|show(?: me)?|tell me) (?:the )?number of (?P<metric>.+?)(?: (?:by|per|across|for each) (?P<group>.+))?",
        r"(?:on average how much) (?P<metric>.+?)(?: (?:by|per|across|for each) (?P<group>.+))?",
        r"(?P<metric>.+?) on average(?: (?:by|per|across|for each) (?P<group>.+))?",
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, clean, re.I)
        if not match:
            continue
        target, group = match.group("metric"), match.group("group")
        masked = clean[:match.start("metric")] + "metric" + clean[match.end("metric"):]
        if group:
            masked = re.sub(r" (?:by|per|across|for each|broken down by) .+$", " by group", masked, flags=re.I)
        operation, _ = predict(masked)
        if operation:
            return {"operation": operation, "column": None if operation == "count" and normalize(target) in {"rows", "records", "entries"} else target, "group_by": group}
    vague = re.fullmatch(r"(?:(?:what (?:is|was|are)|show(?: me)?|tell me|give me) (?:the |our )?)?(.+?)(?: (?:made|generated|earned))?", clean, re.I)
    if vague:
        target = vague[1]
        # Default-to-total is restricted to financial metric concepts and recorded.
        if tokens(target) & {CANONICAL["revenue"], CANONICAL["profit"], CANONICAL["cost"]}:
            masked = clean[:vague.start(1)] + "metric" + clean[vague.end(1):]
            operation, _ = predict(masked)
            if operation == "sum" or normalize(clean) == normalize(target):
                return {"operation": "sum", "column": target, "group_by": None, "implicit_total": True}
    return {"error": "I could not identify a complete supported calculation. Try asking for totals, averages, counts, minimums or maximums, optionally by a category. For example: 'How much revenue did we make by region?'"}


def quarter_filters(table, scope, answers, preferences):
    from .planner import Decision, Choice, refuse
    dates = [c for c in table["columns"] if c["inferred_type"] in {"date", "datetime"}]
    preferred = [c for c in dates if normalize(c["name"]) in {"quarter", "fiscal quarter", "reporting date", "period end"}]
    dates = preferred or dates
    question = "Which date field defines the reporting quarter?"
    selected = next((a["value"] for a in reversed(answers) if a["question"] == question), None)
    if selected:
        dates = [c for c in dates if selected in {c["name"], choice_id("quarter", table["id"], c["name"])}]
    if not dates:
        return refuse("A last-quarter answer needs an ISO date or datetime field identifying each reporting period. No usable reporting date was found; specify a date field or upload dated records.")
    if len(dates) > 1:
        return Decision(action="clarify", reason="Several date fields could define the quarter.", clarification_question=question, choices=[Choice(id=choice_id("quarter", table["id"], c["name"]), label=c["name"], description="Use this reporting date") for c in dates[:10]], query=None, assumptions=[])
    column = dates[0]
    try:
        latest = datetime.fromisoformat(column["max"])
        if latest.tzinfo or latest.time().isoformat() != "00:00:00":
            raise ValueError()
    except (ValueError, TypeError):
        return refuse("Reporting dates must be unambiguous ISO dates or naive midnight timestamps to select a calendar quarter.")
    start = date(latest.year, ((latest.month - 1) // 3) * 3 + 1, 1)
    if scope == "previous":
        end = start
        start = date(start.year - 1, 10, 1) if start.month == 1 else date(start.year, start.month - 3, 1)
    else:
        end = date(start.year + 1, 1, 1) if start.month == 10 else date(start.year, start.month + 3, 1)
    label = f"Q{(start.month - 1) // 3 + 1} {start.year}"
    explanation = f'Interpreted "last/latest quarter" as the latest calendar quarter present in the dataset, not the current wall-clock quarter; selected {label} on "{column["name"]}" ({start} inclusive to {end} exclusive).'
    if scope == "previous":
        explanation = f'Selected the calendar quarter before the latest dataset quarter: {label} on "{column["name"]}" ({start} inclusive to {end} exclusive).'
    confirm_question = f"Use {label} from the uploaded dataset as the {scope} quarter?"
    confirmed = any(a["question"] == confirm_question and a["value"] == "confirm_period" for a in answers)
    if (preferences or {}).get("allow_explicit_assumptions") is False and not confirmed:
        return Decision(action="clarify", reason=explanation, clarification_question=confirm_question, choices=[Choice(id="confirm_period", label=f"Use {label}", description=f"{start} to {end}, exclusive")], query=None, assumptions=[])
    return ([{"column": column["name"], "operator": "ge", "value": start.isoformat()}, {"column": column["name"], "operator": "lt", "value": end.isoformat()}], explanation, label)


def decide(question, profile, answers, preferences=None):
    from .planner import Choice, Decision, Query, refuse
    from .dataset_vocabulary import build_vocabulary, value_matches, value_suggestions
    from .recovery import currency_context, conversion_decision
    conversion = currency_context(question,profile,answers,preferences)
    if conversion is not None:
        return conversion_decision(conversion,answers,preferences)
    request = request_from_language(question)
    if "error" in request:
        return refuse(request["error"])
    value_filters = request.pop('value_filters', [])
    if len(value_filters) > 10:
        return refuse('Use at most ten record filters in one question.')
    vocabulary = profile.get('_vocabulary') or build_vocabulary(profile)
    tables = profile["tables"]
    selected = [t for t in tables if any(a["question"] == "Which table should be analyzed?" and a["value"] in {t["id"], t["name"]} for a in answers)]
    if selected:
        tables = selected
    slots = [("column", request["column"], request["operation"] != "count")]
    if request.get("group_by"):
        slots.append(("group_by", request["group_by"], False))
    if request.get("denominator"):
        slots.append(("denominator", request["denominator"], True))
    if request.get("period"):
        slots.append(("period", request["period"]["column"], False))
    for i, aggregate in enumerate(request.get("additional_aggregates", [])):
        slots.append((f"additional_{i}", aggregate["column"], aggregate["operation"] != "count"))
    candidates, problems = [], []
    for table in tables:
        resolved, assumptions, pending = {}, [], []
        resolved['_filters'] = []
        possible = True
        for i, scope in enumerate(value_filters):
            allowed = matches(scope['field'], table['columns']) if scope['field'] else table['columns']
            options = [option for option in value_matches(scope['phrase'], table, vocabulary) if option[0] in {c['name'] for c in allowed}]
            suggested = False
            if not options and scope.get('operator','eq') == 'eq':
                options = [o for o in value_suggestions(scope['phrase'],table,vocabulary) if o[0] in {c['name'] for c in allowed}]
                suggested = bool(options)
            if scope['field'] and re.fullmatch(r'[-+]?\d+(?:\.\d+)?',scope['phrase'].strip()):
                number = float(scope['phrase'])
                safe_number = math.isfinite(number) and abs(number) <= 2**53 and Decimal(str(number)) == Decimal(scope['phrase'])
                options = [(c['name'],number) for c in allowed if c['inferred_type'] in NUMERIC] if safe_number else []
            elif scope.get('operator','eq') != 'eq':
                options = []
            question_text = f'Which field should "{scope["phrase"]}" select?'
            answer = next((a['value'] for a in reversed(answers) if a['question'] == question_text), None)
            def option_label(option):
                return f'{option[0]} = {option[1]}'
            if answer:
                options = [o for o in options if answer in {choice_id(f'filter_{i}', table['id'], option_label(o)), option_label(o)}]
            if not options:
                problems.append(f'In "{table["name"]}", "{scope["phrase"]}" was not found among indexed category values. Specify a category value shown in your data; at most 256 distinct values per field are indexed.')
                possible = False
                break
            if len(options) > 1 or (not answer and (suggested or (preferences or {}).get('allow_explicit_assumptions') is False)):
                pending.append((f'filter_{i}', scope['phrase'], question_text, [{'name':option_label(o),'inferred_type':'record selection'} for o in options]))
                continue
            name, value = options[0]
            resolved['_filters'].append({'column':name,'operator':scope.get('operator','eq'),'value':value})
            if (preferences or {}).get('allow_explicit_assumptions') is not False:
                assumptions.append(f'Selected records using the requested condition on "{name}" and value {value!r}.')
        if not possible:
            continue
        for slot, phrase, numeric in slots:
            if phrase is None:
                resolved[slot] = None
                continue
            fields = matches(phrase, table["columns"], numeric)
            approximate = False
            if not fields:
                # Misspellings suggest fields but always require a human choice.
                desired = normalize(phrase)
                fields = [c for c in table['columns'] if (not numeric or c['inferred_type'] in NUMERIC) and SequenceMatcher(None, desired, normalize(c['name'])).ratio() >= .78]
                approximate = bool(fields)
            if request["operation"] == "count" and slot == "column" and len(fields) > 1:
                identifiers = [c for c in fields if "id" in normalize(c["name"]).split()]
                if len(identifiers) == 1:
                    fields = identifiers
            # Count entities only via an identifiable ID/name field, never generic rows.
            if not fields:
                nonnumeric = matches(phrase, table["columns"], False) if numeric else []
                if nonnumeric:
                    details = ", ".join(f'"{c["name"]}" ({c["inferred_type"]})' for c in nonnumeric[:4])
                    problems.append(f'In "{table["name"]}", "{phrase}" matches {details}, but {request["operation"]} requires numeric values. Currency symbols, comma-formatted money, percentages and mixed text are preserved as text; convert a copy to plain numeric values with explicit units before uploading.')
                else:
                    fields_available = ", ".join(f'"{c["name"]}"' for c in table["columns"][:8])
                    problems.append(f'In "{table["name"]}", no {"numeric " if numeric else ""}field matches {slot} "{phrase}". Available columns: {fields_available}.')
                possible = False
                break
            question_text = f'Which field should "{phrase}" mean for {slot}?'
            answer = next((a["value"] for a in reversed(answers) if a["question"] == question_text), None)
            if answer:
                fields = [c for c in fields if answer in {choice_id(slot, table["id"], c["name"]), c["name"]}]
                if not fields:
                    possible = False
                    break
            uncertain = len(fields) > 1
            renamed = len(fields) == 1 and phrase.casefold().strip('`" ') != fields[0]["name"].casefold()
            if uncertain or (approximate and not answer) or (renamed and not answer and (preferences or {}).get("allow_explicit_assumptions") is False):
                pending.append((slot, phrase, question_text, fields))
                continue
            field = fields[0]
            if slot == "period" and field["inferred_type"] not in {"date", "datetime"}:
                possible = False
                break
            resolved[slot] = field["name"]
            if (renamed or answer) and (preferences or {}).get("allow_explicit_assumptions") is not False:
                assumptions.append(f'Interpreted "{phrase}" as column "{field["name"]}" in table "{table["name"]}"' + (" after your confirmation." if answer else "."))
                if request["operation"] == "count" and slot == "column":
                    assumptions.append(f'Count means non-null values in "{field["name"]}", not distinct entities.')
        if possible:
            candidates.append((table, resolved, assumptions, pending))
    if not candidates:
        return refuse("I could not map the complete question to a usable table. " + " ".join(problems[:3]) + " Choose an available field and specify the metric and grouping. Example: 'sum <numeric column> by <category column>'.")
    if len(candidates) > 1:
        return Decision(action="clarify", reason="Multiple uploaded tables can answer this question. Files are not implicitly combined or joined.", clarification_question="Which table should be analyzed?", choices=[Choice(id=t["id"], label=t["name"], description=f'{t["row_count"]} rows') for t, *_ in candidates[:10]], query=None, assumptions=[])
    table, fields, assumptions, pending = candidates[0]
    if pending:
        slot, phrase, question_text, choices = pending[0]
        return Decision(action="clarify", reason="Confirm the business meaning before calculating.", clarification_question=question_text, choices=[Choice(id=choice_id(slot, table["id"], c["name"]), label=c["name"], description=f'{table["name"]} · {c["inferred_type"]}') for c in choices[:10]], query=None, assumptions=[])
    score = .9
    implicit_total = request.pop("implicit_total", False)
    quarter_scope = request.pop("quarter_scope", None)
    quarter_comparison = request.pop("quarter_comparison", False)
    annual_comparison = request.pop('annual_comparison', None)
    filters = fields['_filters']
    if annual_comparison is not None:
        year_fields = [c for c in table['columns'] if normalize(c['name']) in {'year','fiscal year','reporting year'} and c['inferred_type'] in NUMERIC | {'date','datetime'}]
        if len(year_fields) != 1:
            return refuse('This comparison needs one clearly identified Year or Fiscal Year field. Specify the reporting year field in your dataset.')
        year_field = year_fields[0]
        if not annual_comparison:
            indexed = [item['value'] for item in vocabulary.get(table['id'],{}).get(year_field['name'],{}).get('values',[])]
            years = sorted({int(v) for v in indexed or year_field.get('sample_values',[]) if isinstance(v,(int,float)) and not isinstance(v,bool) and v == int(v) and 1 <= v <= 9998})
            if len(years) < 2:
                return refuse('Specify two reporting years to calculate a growth rate.')
            question_text = 'Which reporting years should define the growth rate?'
            chosen = next((a['value'] for a in reversed(answers) if a['question'] == question_text),None)
            baseline,current = years[-2:]
            explicit = re.fullmatch(r'(\d{4})\s+(?:to|and)\s+(\d{4})',chosen or '',re.I)
            if explicit:
                baseline,current = map(int,explicit.groups())
            if not explicit and chosen != f'years_{baseline}_{current}':
                return Decision(action='clarify',reason='A growth rate needs a baseline and comparison year.',clarification_question=question_text,choices=[Choice(id=f'years_{baseline}_{current}',label=f'{baseline} to {current}',description='Use the two latest years in the dataset preview, or enter two years as YYYY to YYYY.')],query=None,assumptions=[])
            annual_comparison = [baseline,current]
        baseline,current = annual_comparison
        if not 1 <= baseline < current <= 9998:
            return refuse('Specify two valid reporting years in chronological order, for example 2021 to 2022.')
        request['period'] = {'kind':'year' if year_field['inferred_type'] in NUMERIC else 'calendar','column':year_field['name'],'baseline_start':f'{baseline:04d}-01-01','baseline_end':f'{baseline+1:04d}-01-01','current_start':f'{current:04d}-01-01','current_end':f'{current+1:04d}-01-01'}
        fields['period'] = year_field['name']
    if value_filters:
        score = min(score, .8)
    if quarter_comparison:
        latest = quarter_filters(table, "latest", answers, preferences)
        previous = quarter_filters(table, "previous", answers, preferences)
        if isinstance(latest, Decision):
            return latest
        if isinstance(previous, Decision):
            return previous
        request["period"] = {"column": latest[0][0]["column"], "baseline_start": previous[0][0]["value"], "baseline_end": previous[0][1]["value"], "current_start": latest[0][0]["value"], "current_end": latest[0][1]["value"]}
        fields["period"] = request["period"]["column"]
        assumptions += [previous[1], latest[1]]
        score = min(score, .6)
    if implicit_total:
        question_text = "Interpret this financial metric question as a total?"
        confirmed = any(a["question"] == question_text and a["value"] == "confirm_total" for a in answers)
        if (preferences or {}).get("allow_explicit_assumptions") is False and not confirmed:
            return Decision(action="clarify", reason="The aggregation was not specified.", clarification_question=question_text, choices=[Choice(id="confirm_total", label="Use the total", description="Sum the selected financial column")], query=None, assumptions=[])
        if (preferences or {}).get("allow_explicit_assumptions") is not False:
            assumptions.append("The aggregation was unspecified; interpreted this financial metric as a sum of the selected records.")
        score = .45
    if quarter_scope:
        period = quarter_filters(table, quarter_scope, answers, preferences)
        if isinstance(period, Decision):
            return period
        period_filters, explanation, label = period
        filters += period_filters
        if (preferences or {}).get("allow_explicit_assumptions") is not False:
            assumptions.append(explanation)
        score = min(score, .6)
    request = {**request, "table": table["id"], "column": fields["column"], "group_by": fields.get("group_by"), "filters": [], "join": None}
    request["filters"] = filters
    if request.get("denominator"):
        request["denominator"] = fields["denominator"]
    if request.get("period"):
        request["period"] = {**request["period"], "column": fields["period"]}
    request["additional_aggregates"] = [{**a, "column": fields[f"additional_{i}"]} for i, a in enumerate(request.get("additional_aggregates", []))]
    return Decision(action="answer", reason="Grounded a supported natural-language request in the uploaded schema.", clarification_question=None, choices=[], query=Query.model_validate(request), assumptions=assumptions[:10], interpretation_score=score)
