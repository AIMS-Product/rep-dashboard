from html import escape
import json

def local_manager(template, data):
    page = bind_payload(template, data)
    page = replace_once(page, "            const resp = await fetch(dataFile + '?t=' + Date.now());\n"
                        '            closeMetricDrawer();\n            DATA = await resp.json();',
                        '            closeMetricDrawer();\n'
                        '            DATA = JSON.parse(document.getElementById("shared-dashboard-data").textContent);')
    page = page.replace('>Booked</th>', '>Calls Occurred</th>')
    page = page.replace("booked: 'Booked'", "booked: 'Calls Occurred'")
    page = page.replace("'Lane', 'Booked', 'Shown'", "'Lane', 'Calls Occurred', 'Shown'")
    historical = '''
        const historical = DATA.historical_closers;
        if (historical.names.length) {
            html += '<tr class="lane-totals-row shared-historical"><td>Historical closers</td>'
                + '<td class="g-activity-start">' + historical.occurred_calls + '</td>'
                + '<td>' + historical.shown + '</td><td>' + historical.qualified + '</td>'
                + '<td>' + historical.deals + '</td><td class="g-activity-end">' + fmtK(historical.revenue) + '</td>'
                + '<td>' + (percent(historical.close_rate) || '—') + '</td>'
                + '<td>' + (percent(historical.show_rate) || '—') + '</td>'
                + '<td>' + (percent(historical.qualification_rate) || '—') + '</td><td colspan="6">—</td></tr>';
        }

'''
    page = replace_once(page, '        // ── Combined Team Totals ──', historical + '        // ── Combined Team Totals ──')
    export_history = '''
        const historical = DATA.historical_closers;
        if (historical.names.length) {
            rows.push(['Historical closers', '', historical.occurred_calls, historical.shown, historical.qualified,
                historical.deals, historical.revenue, percent(historical.close_rate), percent(historical.show_rate),
                percent(historical.qualification_rate), '', '', '', '', '', '']);
        }

'''
    page = replace_once(page, '        // Add totals row', export_history + '        // Add totals row')
    page = page.replace('    setInterval(fetchData, 5 * 60 * 1000);', '')
    page = page.replace('href="archive.html"', 'href="https://aims-product.github.io/sales-manager-dashboard/archive.html"')
    return preview_banner(page, data['metric_definitions']['cutoff_exclusive'], data['auxiliary_snapshot'])



def bind_payload(page, data):
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    insert = '<script type="application/json" id="shared-dashboard-data">' + payload + '</script>\n'
    return replace_once(page, '<script>', insert + '<script>\nconst percent = value => value == null ? "" : value + "%";')


def preview_banner(page, cutoff, auxiliary=None):
    timestamp = escape(cutoff.replace('T', ' '))
    note = f' · Task and pipeline evidence observed {escape(auxiliary)}' if auxiliary else ''
    banner = (
        '<aside aria-label="Shared reporting source" style="padding:8px 16px;text-align:center;'
        'font:12px/1.5 system-ui,sans-serif;background:#242c3c;color:white">'
        f'Shared Close snapshot {timestamp}{note}</aside>'
    )
    return replace_once(page, '<body>', '<body>\n' + banner)


def replace_once(text, marker, replacement, *, expected=1):
    if text.count(marker) != expected:
        raise ValueError(f'Preview template changed near {marker!r}')
    return text.replace(marker, replacement)
