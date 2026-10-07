"""Typed missing-input recovery. Client-supplied code is never executed."""
import re
from .planner import refuse

PATTERN=r'(.+?)\s+(?:in|converted to|convert to)\s+(USD|EUR|GBP|INR)\s*[?.!]*'


def currency_context(question,profile,answers,preferences):
    match=re.fullmatch(PATTERN,question.strip(),re.I)
    if not match:return None
    from .natural_planner import decide
    base=decide(match[1],profile,answers,preferences)
    if base.action!='answer':return (base,None,None)
    q=base.query
    if q.operation!='sum' or q.additional_aggregates or q.join:
        return (refuse('Currency conversion currently supports one column total, optionally filtered or grouped. Ask for total <money field> in <currency code>.'),None,None)
    codes={w['code'].removeprefix('CURRENCY_FORMAT_') for w in profile['warnings'] if w.get('table_id')==q.table and w.get('column')==q.column and w['code'].startswith('CURRENCY_FORMAT_')}
    header=re.search(r'\b(USD|EUR|GBP|INR)\b',q.column,re.I)
    if header:codes.add(header[1].upper())
    codes &= {'USD','EUR','GBP','INR'}
    if len(codes)!=1:return (refuse('Provide an explicit source currency (USD, EUR, GBP or INR) in the money column header or values before requesting conversion. A dollar symbol alone does not establish USD.'),None,None)
    return base,next(iter(codes)),match[2].upper()


def conversion_decision(context,answers,preferences=None):
    base,source,target=context
    if source is None:return base
    if source==target:return base
    label=f'Exchange rate {source} to {target}'
    value=next((a['value'] for a in reversed(answers) if a['question']==label),None)
    if value is None:
        return refuse(f'Provide the {source}→{target} rate for the selected records and its source, and this total can be calculated. If rates vary across the selected periods, filter to one rate period or supply converted data; no rate is assumed.')
    from .planner import CurrencyConversion
    conversion=CurrencyConversion(source=source,target=target,rate=float(value))
    return base.model_copy(update={'query':base.query.model_copy(update={'conversion':conversion}),
        'assumptions':base.assumptions+([f'Applied your supplied {source}→{target} rate {value} uniformly to the selected records.'] if (preferences or {}).get('allow_explicit_assumptions') is not False else []), 'interpretation_score':min(base.interpretation_score or 1,.7)})


def recovery_for(question,profile,answers,reason):
    context=currency_context(question,profile,answers,{})
    if context and context[1] and context[1]!=context[2] and context[0].action=='answer':
        base,source,target=context
        label=f'Exchange rate {source} to {target}'
        if not any(a['question']==label for a in answers):
            code='''from decimal import Decimal

def answer(amounts, exchange_rate):
    rate = Decimal(str(exchange_rate))
    if not rate.is_finite() or not 0 < rate <= 1000000:
        raise ValueError("Supply a positive, finite exchange rate up to 1000000")
    total = sum((Decimal(str(v)) for v in amounts if v is not None), Decimal(0))
    return total * rate
'''
            return {'requirements':[f'Provide {source}→{target} as {target} per 1 {source}, for the selected records.', 'Provide the rate source or provenance note. Use one rate period; filter the question if different periods have different rates.'],
                'parameters':[{'name':'rate','label':label,'unit':f'{target} per {source}'}], 'code_template':code,'resume_url':None,'ready_to_run':False,
                'source_currency':source,'target_currency':target,'query':base.query.model_dump()}
    if 'duplicate' in reason.lower():remedy='Upload a corrected dataset with the intended unique records or a confirmed identifier; then rerun the same question.'
    elif 'period' in reason.lower() or 'date' in reason.lower():remedy='Provide valid reporting dates and complete values for both requested periods, then rerun.'
    elif 'numeric' in reason.lower():remedy='Upload a copy with numeric amounts and explicit units, then rerun the requested metric.'
    else:remedy='Resolve the issue stated above using the available source fields or corrected data, then ask the complete calculation again.'
    return {'requirements':[reason,remedy],'parameters':[],'code_template':None,'resume_url':None,'ready_to_run':False}
