"""Portable replay of the actual approved query and normalized source records."""
import json


def generate(sql, profile, tables, used_ids):
    sources = {t['id']: {'columns': [c['name'] for c in t['columns']],
                           'rows': tables[t['id']]}
               for t in profile['tables'] if t['id'] in used_ids}
    return ('# AuditCode: reproduce this calculation using the original normalized records.\n'
            '# Install dependencies: python -m pip install duckdb pandas\n'
            '# Save as audit_code.py, then run: python audit_code.py\n'
            'import json\nimport duckdb\nimport pandas as pd\n\n'
            'sources = json.loads(' + repr(json.dumps(sources, ensure_ascii=True)) + ')\n'
            'sql = ' + repr(sql) + '\n\n'
            'with duckdb.connect(config={"enable_external_access": "false"}) as connection:\n'
            '    for name, source in sources.items():\n'
            '        frame = pd.DataFrame(source["rows"], columns=source["columns"], dtype=object)\n'
            '        connection.register(name, frame)\n'
            '    print(connection.execute(sql).fetchdf().to_string(index=False))\n')
