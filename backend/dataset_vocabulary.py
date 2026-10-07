"""Bounded local search metadata; cell text is data, never instructions."""
import re
import unicodedata
from difflib import SequenceMatcher


def words(value):
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


def build_vocabulary(profile, tables=None):
    result = {}
    for table in profile['tables']:
        fields = {}
        for column in table['columns']:
            values = []
            if column['inferred_type'] in {'string', 'boolean', 'date', 'datetime'} or words(column['name']) in {'year','fiscal year','reporting year'}:
                source = (r[column['name']] for r in tables[table['id']]) if tables is not None else column.get('sample_values', [])
                seen = set()
                for value in source:
                    if value is None or len(str(value)) > 160:
                        continue
                    key = words(value)
                    if key and key not in seen:
                        seen.add(key)
                        values.append({'keyword': key, 'value': value})
                        if len(values) == 256:
                            break
            fields[column['name']] = {'keywords': words(column['name']).split(), 'values': values}
        result[table['id']] = fields
    return result


def value_matches(phrase, table, vocabulary, column=None):
    key = words(phrase.strip('"\' '))
    matches = []
    for name, field in vocabulary.get(table['id'], {}).items():
        if column is not None and name != column:
            continue
        for item in field['values']:
            if key == item['keyword']:
                matches.append((name, item['value']))
    return matches


def value_suggestions(phrase, table, vocabulary):
    key = words(phrase.strip('"\' '))
    directional = {'western region':'west','eastern region':'east','northern region':'north','southern region':'south'}
    alias = directional.get(key)
    return [(name,item['value']) for name, field in vocabulary.get(table['id'],{}).items() for item in field['values']
        if alias == item['keyword'] or (len(key) >= 4 and SequenceMatcher(None,key,item['keyword']).ratio() >= .8)]
