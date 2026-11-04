"""Self-contained, offline AWR transfer report and evidence exports."""
import io
import json
import zipfile
from pathlib import Path

from awr_transfer_data import dataset_csvs, json_default, query_ledger_sql

ROOT = Path(__file__).resolve().parent.parent


def render_report(dataset, ledger):
    payload = json.dumps({'dataset': dataset, 'sql': query_ledger_sql(ledger)},
                         default=json_default, allow_nan=False).replace('<', '\\u003c')
    template = (ROOT / 'html' / 'awr_transfer.html').read_text(encoding='utf-8')
    # Replace from the template once. Data cannot manufacture a second marker.
    replacements = {
        '/* AWR_STYLE */': (ROOT / 'html' / 'awr_transfer.css').read_text(encoding='utf-8'),
        '/* ECHARTS_LIBRARY */': (ROOT / 'html' / 'vendor' / 'echarts-6.1.0.min.js').read_text(encoding='utf-8'),
        '/* AWR_SCRIPT */': (ROOT / 'html' / 'awr_transfer.js').read_text(encoding='utf-8'),
        'AWR_PAYLOAD_JSON': payload,
    }
    import re
    return re.sub('|'.join(re.escape(key) for key in replacements), lambda match: replacements[match[0]], template)


def report_bundle(dataset, ledger, html=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('awr-rac-transfer.html', html or render_report(dataset, ledger))
        archive.writestr('queries.sql', query_ledger_sql(ledger))
        archive.writestr('metadata.json', json.dumps({key: value for key, value in dataset.items() if key != 'frames'}, default=json_default, indent=2))
        for name, contents in dataset_csvs(dataset).items():
            archive.writestr(name, contents)
    return output.getvalue()
