"""Bounded adversarial review and measured data-quality counterfactuals."""
import math
from .common import canonical, digest


def semantics_hash(q, profile):
    used={q.table} | ({q.join.table} if q.join else set())
    return digest({'query':q.model_dump(),'fields':[t['columns'] for t in profile['tables'] if t['id'] in used],
        'units':[w for w in profile['warnings'] if w.get('table_id') in used and w['code'].startswith('CURRENCY_')]})


def assess(q, profile, tables, baseline):
    from .engine import independent, equivalent, selected_rows, UnsafePlan
    used=[q.table]+([q.join.table] if q.join else [])
    checks=[]; scenarios=[]
    def run(name, changed, gate, assumption=None, query=None):
        try:
            if query is not None:
                from .engine import prepare
                prepare(query.model_dump(),profile,changed)
            alternative=independent(query or q,changed)
            same=equivalent(baseline,alternative)
            if gate:
                checks.append({'name':name,'status':'passed' if same else 'failed','detail':'Result unchanged.' if same else 'The result changes under this plausible data interpretation.'})
            scenarios.append({'name':name,'assumption':assumption,'rows':alternative,'changed':not same})
        except UnsafePlan as exc:
            if gate:checks.append({'name':name,'status':'failed','detail':str(exc)})
            scenarios.append({'name':name,'assumption':assumption,'rows':None,'changed':True})
    run('Reverse source record order',{**tables,**{name:list(reversed(tables[name])) for name in used}},True)
    removed=0;unique={}
    for name in used:
        seen=set();unique[name]=[]
        for row in tables[name]:
            key=canonical(row)
            if key not in seen:seen.add(key);unique[name].append(row)
        removed+=len(tables[name])-len(unique[name])
    if removed:run(f'Remove {removed} exact duplicate source rows',{**tables,**unique},True)
    else:checks.append({'name':'Exact duplicate removal','status':'passed','detail':'No exact duplicate source rows.'})
    by_id={t['id']:t for t in profile['tables']}
    for name in used:
        keys=[c['name'] for c in by_id[name]['columns'] if c['name'].casefold().replace('_',' ').endswith('id') and c['inferred_type'] in {'string','integer'}][:1]
        for key in keys:
            seen=set();rows=[]
            for row in tables[name]:
                if row[key] is None:rows.append(row);continue
                marker=canonical(row[key])
                if marker not in seen:seen.add(marker);rows.append(row)
            if len(rows)<len(tables[name]):run(f'Deduplicate by {key} in {by_id[name]["name"]}',{**tables,name:rows},True)
    date_tables={};converted_dates=0
    from datetime import datetime
    for name in used:
        fields=[c['name'] for c in by_id[name]['columns'] if c['inferred_type'] in {'date','datetime'}]
        if not fields:
            date_tables[name]=tables[name];continue
        rows=[]
        for row in tables[name]:
            changed=dict(row)
            for field in fields:
                value=row[field]
                if isinstance(value,str) and 'T' in value:
                    try:
                        timestamp=datetime.fromisoformat(value)
                        if timestamp.tzinfo is None and timestamp.time().isoformat()=='00:00:00':
                            changed[field]=timestamp.date().isoformat();converted_dates+=1
                    except ValueError:pass
            rows.append(changed)
        date_tables[name]=rows
    if converted_dates:run('Read naive midnight timestamps as equivalent ISO calendar dates',{**tables,**date_tables},True)
    checks.append({'name':'Date interpretation','status':'passed','detail':'Ambiguous date order is never guessed; period inputs require ISO dates or whole reporting years.'})
    if q.join:
        right={c['name']:c for c in by_id[q.join.table]['columns']}
        for column in by_id[q.table]['columns']:
            name=column['name']
            if name==q.join.left_column or name not in right or not right[name]['candidate_key'] or column['inferred_type']!=right[name]['inferred_type']:continue
            if not name.casefold().replace('_',' ').endswith('id'):continue
            alternate=q.model_copy(update={'join':q.join.model_copy(update={'left_column':name,'right_column':name})})
            # Only challenge alternatives that satisfy the same key-coverage safety checks.
            from .engine import prepare
            try:prepare(alternate.model_dump(),profile,tables)
            except UnsafePlan:continue
            run(f'Join using alternative shared identifier {name}',tables,True,query=alternate)
            break
    checks.append({'name':'Join interpretation','status':'passed','detail':'No join requested.' if not q.join else 'Only complete, unique right-key joins are eligible; any tested alternative shared identifier is reported separately.'})
    selected=selected_rows(q,tables)
    if q.period:
        from .engine import period_day
        selected=[r for r in selected if q.period.baseline_start<=period_day(r[q.period.column],q.period.kind)<q.period.baseline_end or q.period.current_start<=period_day(r[q.period.column],q.period.kind)<q.period.current_end]
    numeric=[c for c in [q.column,q.denominator]+[a.column for a in q.additional_aggregates] if c]
    unknown=[]
    for column in dict.fromkeys(numeric):
        missing=any(r.get(column) is None for r in selected)
        if not missing and any(f.column==column for f in q.filters):
            missing=any(r.get(column) is None for r in selected_rows(q.model_copy(update={'filters':[]}),tables))
        if not missing:continue
        owner=q.table;field=column
        if q.join and column.startswith(q.join.table+'.'):owner=q.join.table;field=column[len(owner)+1:]
        values=[r[field] for r in tables[owner] if isinstance(r[field],(int,float)) and not isinstance(r[field],bool)]
        if not values:
            unknown.append(f'Missing {column} values have no observed numeric range; their impact cannot be bounded.');continue
        unknown.append(f'Missing {column} values have no established bounds. Observed-range scenarios below are assumptions, not guaranteed limits.')
        for value in [min(values),max(values)]:
            run(f'Fill missing {column} with {value}',{**tables,owner:[{**r,field:value} if r[field] is None else r for r in tables[owner]]},False,'Assume missing values lie within the observed source range.')
    components=[]
    for row in baseline:
        for key,value in row.items():
            if key=='group_value':continue
            impacts=[]
            for scenario in scenarios:
                alternative=next((r for r in scenario['rows'] or [] if canonical(r.get('group_value'))==canonical(row.get('group_value'))),None)
                candidate=alternative.get(key) if alternative else None
                delta=candidate-value if isinstance(value,(int,float)) and isinstance(candidate,(int,float)) else None
                percent=delta/abs(value)*100 if delta is not None and value!=0 else None
                if percent is not None and not math.isfinite(percent):percent=None
                impacts.append({'scenario':scenario['name'],'alternative':candidate,'delta':delta,'percent_change':percent,'assumption':scenario['assumption']})
            measured=[i for i in impacts if i['delta'] is not None and i['assumption'] is None]
            largest=max(measured,key=lambda i:abs(i['delta']),default=None)
            ranking=largest['scenario'] if largest and largest['delta'] else 'No change in tested non-assumed scenarios.'
            if unknown:ranking='Cannot fully rank: missing inputs have unknown bounds. Largest measured challenge: '+ranking
            components.append({'key':key,'group':row.get('group_value'),'candidate_value':value,'most_sensitive':ranking,'impacts':impacts})
    if q.group_by:
        impacts=[]
        for scenario in scenarios:
            count=len(scenario['rows']) if scenario['rows'] is not None else None
            delta=count-len(baseline) if count is not None else None
            impacts.append({'scenario':scenario['name'],'alternative':count,'delta':delta,'percent_change':delta/len(baseline)*100 if delta is not None and baseline else None,'assumption':scenario['assumption']})
        biggest=max((i for i in impacts if i['delta'] is not None),key=lambda i:abs(i['delta']),default=None)
        components.append({'key':'group_count','group':None,'candidate_value':len(baseline),'most_sensitive':biggest['scenario'] if biggest and biggest['delta'] else 'No change in tested group-count scenarios.','impacts':impacts})
    return {'status':'passed' if all(c['status']=='passed' for c in checks) else 'failed','checks':checks,'sensitivity':components,'unknown_impacts':unknown,
        'scope':'Bounded deterministic adversarial reviewer; checks do not establish correctness against every possible interpretation.'}
