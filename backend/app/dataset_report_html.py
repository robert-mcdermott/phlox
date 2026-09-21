"""Static accessible HTML; every label is escaped and charts use the computed table values."""
from decimal import Decimal
from html import escape
import json

from app.dataset_analysis import display

STYLE = '''
:root{color-scheme:light;--ink:#172b3a;--muted:#526678;--line:#d8e2e9;--accent:#17699b;--paper:#fff;--bg:#f3f7fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,sans-serif;overflow-wrap:anywhere}
main{max-width:1120px;margin:auto;padding:40px 24px}header{padding:16px 0 30px;border-bottom:2px solid var(--line)}
h1{font-size:clamp(1.8rem,4vw,2.8rem);line-height:1.15;margin:12px 0;overflow-wrap:anywhere}h2{font-size:1.4rem;margin:0 0 12px}
.kicker{color:var(--accent);font-size:.8rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase}
.notice{border-left:4px solid var(--accent);padding:12px 16px;background:#e8f2f8}.muted{color:var(--muted)}
.stats{display:flex;flex-wrap:wrap;gap:16px;margin:24px 0}.stat{flex:1;min-width:150px;background:var(--paper);padding:18px;border:1px solid var(--line);border-radius:10px}.stat strong{display:block;font-size:1.8rem}
section{margin:24px 0;padding:24px;background:var(--paper);border:1px solid var(--line);border-radius:12px}
.chart{margin:20px 0}.bar-row{display:grid;grid-template-columns:minmax(90px,30%) 1fr minmax(40px,18%);gap:12px;align-items:center;margin:10px 0;font-size:.85rem}.bar-label,.bar-value{overflow-wrap:anywhere}.bar-value{text-align:right;font-variant-numeric:tabular-nums}.track{height:18px;position:relative;background:#eef3f6;border-radius:3px}.bar{height:100%;position:absolute;background:var(--accent);border-radius:3px}.axis{position:absolute;height:100%;border-left:1px solid var(--muted)}
.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.88rem}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);overflow-wrap:anywhere;max-width:380px}th{background:#f3f7fa;font-weight:650}td.num{text-align:right;font-variant-numeric:tabular-nums}caption{text-align:left;margin-bottom:8px;color:var(--muted)}a{color:var(--accent);overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8rem}details{margin:16px 0}summary{cursor:pointer;font-weight:600}footer{font-size:.85rem;color:var(--muted)}
@media(max-width:600px){main{padding:20px 12px}section{padding:16px}.bar-row{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:6px}.track{grid-column:1/-1;grid-row:2}.bar-value{grid-column:2;grid-row:1}table{min-width:560px}th,td{padding:8px}}
@media print{body{background:white}main{max-width:none;padding:0}section{break-inside:avoid}.table-wrap{overflow:visible}}
'''


def e(value):
    return escape(str(value), quote=True)


def chart(section):
    groups = section['groups'][:20]
    known = [Decimal(str(g['value'])) for g in groups if g['value'] is not None]
    largest = max((abs(value) for value in known), default=Decimal(0))
    signed = any(value < 0 for value in known)
    bars = []
    for group in groups:
        value = group['value']
        width = 0 if value is None or not largest else float(abs(Decimal(str(value))) / largest) * (50 if signed else 100)
        left = (50 - width if value is not None and Decimal(str(value)) < 0 else 50) if signed else 0
        bars.append(f'<div class="bar-row"><span class="bar-label">{e(display(group["group"]))}</span>'
                    f'<div class="track"><span class="axis" style="left:{50 if signed else 0}%"></span>'
                    f'<span class="bar" style="left:{left:.4f}%;width:{width:.4f}%"></span></div>'
                    f'<span class="bar-value">{e(display(value))}</span></div>')
    note = '<p class="muted">Chart shows the first 20 groups; the table includes every group.</p>' if len(section['groups']) > 20 else ''
    return '<div class="chart" aria-hidden="true">' + ''.join(bars) + '</div>' + note


def render(title, analysis, manifest):
    coverage = manifest['coverage']
    status = 'All API-reported matches captured' if coverage['all_reported_records_captured'] else 'Partial dataset'
    sources = ' '.join(f'<a href="#source-{e(s["label"])}">[{e(s["label"])}]</a>' for s in manifest['sources'])
    sections = []
    for section in analysis['sections']:
        multi = '<p class="notice">Multi-valued categories overlap: a record can appear in several groups. Do not sum group counts as unique records.</p>' if section['multi_valued_groups'] else ''
        numeric = section['metric'] == 'sum'
        rows = []
        for group in section['groups']:
            rows.append('<tr><th scope="row">' + e(display(group['group'])) + '</th><td class="num">' + e(display(group['value'])) + '</td><td class="num">' + str(group['record_count']) + '</td>' +
                        (f'<td class="num">{group["known_count"]}</td><td class="num">{group["missing_count"]}</td>' if numeric else '') + '</tr>')
        if not rows:
            rows.append(f'<tr><td colspan="{5 if numeric else 3}">No records match the selection.</td></tr>')
        label = f'Known sum of {section["value_field"]}' if numeric else 'Record count'
        sections.append(f'<section><h2>{e(section["title"])}</h2><p class="muted">{e(label)} · Group: {e(section["group_by"] or "all selected records")}</p>{multi}' + chart(section) +
            '<div class="table-wrap"><table><caption>Exact values used by the chart</caption><thead><tr><th scope="col">Group</th>' +
            f'<th scope="col">{e(label)}</th><th scope="col">Records</th>' + ('<th scope="col">Known</th><th scope="col">Missing</th>' if numeric else '') +
            '</tr></thead><tbody>' + ''.join(rows) + f'</tbody></table></div><p>Derived from {sources}</p></section>')
    source_list = ''.join(f'<li id="source-{e(s["label"])}"><strong>[{e(s["label"])}]</strong> '
                         f'<a href="{e(s["url"])}" rel="noreferrer noopener">{e(s["url"])}</a> · '
                         f'Captured {e(s["captured_at"])} · records [{s["range"][0]}, {s["range"][1]})'
                         f'<br><small>SHA-256: {e(s["content_sha256"])}</small></li>' for s in manifest['sources'])
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>{e(title)}</title><style>{STYLE}</style></head><body><main><header><div class="kicker">Phlox · Dataset report</div>'
            f'<h1>{e(title)}</h1><p class="notice"><strong>{status}.</strong> {e(manifest["notice"])}</p></header>'
            '<div class="stats">' + ''.join(f'<div class="stat"><strong>{count:,}</strong>{label}</div>' for count, label in [
                (analysis['input_records'], 'Captured records'), (coverage['api_reported_matches'], 'API-reported matches'),
                (analysis['selected_records'], 'Selected for analysis'), (analysis['excluded_records'], 'Excluded by filters')]) + '</div>' +
            ''.join(sections) + '<section><h2>Method and scope</h2><p>' + e(analysis['method']) + '</p>' +
            '<p>Coverage is measured before report filters. Selected results are not a new API match count. Source pages may change between requests.</p>' +
            '<details><summary>Analysis recipe</summary><pre>' + e(json.dumps(manifest['analysis_recipe'], indent=2, ensure_ascii=False)) + '</pre></details>' +
            '<details><summary>Original API query</summary><pre>' + e(json.dumps(manifest['query'], indent=2, ensure_ascii=False)) + '</pre></details></section>' +
            '<section><h2>Sources</h2><ol>' + source_list + '</ol><p>Source links identify API endpoints. Exact retained records, query recipes, selections and hashes are included in the accompanying data files and manifest.</p></section>' +
            '<footer>Generated from validated retained records. No live data is loaded by this report. Numeric tables use exact values; bar lengths are proportional visual approximations.</footer></main></body></html>')
