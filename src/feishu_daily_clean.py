#!/usr/bin/env python3
"""Clean an officially exported Feishu .base snapshot without editing its source.

The output JSON/CSV/TSV files are intermediates for publishing into the user's
own Feishu document. No credentials, network calls, fixed dates or fixed totals.
"""
from __future__ import annotations
import argparse, base64, collections, csv, datetime as dt, decimal, gzip
import hashlib, json, math
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
D = decimal.Decimal
MISSING = {'来源字段未填', '未记录', '缺少标识', '待核实', '待补充', '待确认', '待店方确认', '缺少规则'}

def link_metadata():
    """Optional local provenance; business document URLs never belong in source code."""
    path = Path.cwd() / 'feishu_sync_state.json'
    context = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return {'来源': context.get('source_url', ''), '目标': context.get('target_url', '')}

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def amount(value):
    return D(str(value)) if number(value) else None

def clean_payment(row):
    paid = row.get('系统已支付')
    payable = amount(row.get('订单应付总额'))
    refund = amount(row.get('已退金额'))
    if paid is True and payable is not None and refund is not None:
        net = payable - refund
    elif paid is False:
        net = D(0)
    else:
        net = None
    observed = paid is True and row.get('测试核实状态') != '明确测试标记' if isinstance(paid, bool) else None
    original = amount(row.get('系统支付净额'))
    verified = net is not None and original is not None and abs(net-original) <= D('.01')
    return {'源record_id': row['源record_id'], '支付观察': observed,
            '系统支付净额': scalar(net), '净额核验': '一致' if verified else '待核实'}

def sum_amount(values):
    values = list(values)
    return sum((amount(v) for v in values), D(0)) if all(number(v) for v in values) else None

def scalar(value):
    return float(value) if isinstance(value, D) else value

def decode(field, value):
    if value is None:
        return None
    kind = field['type']
    if kind == 1:
        if not isinstance(value, list) or any(not isinstance(x, dict) or 'text' not in x for x in value):
            raise ValueError('Unsupported rich text: ' + field['name'])
        return ''.join(x['text'] for x in value)
    if kind == 2:
        if not number(value):
            raise ValueError('Invalid number: ' + field['name'])
        return value
    if kind in (3, 4):
        options = {x['id']: x['name'] for x in field.get('property', {}).get('options', [])}
        ids = value if isinstance(value, list) else [value]
        names = [options.get(x, '未解析选项:' + str(x)) for x in ids]
        return names if kind == 4 else names[0]
    if kind == 5:
        if not number(value):
            raise ValueError('Invalid date: ' + field['name'])
        return dt.datetime.fromtimestamp(value / 1000, TZ).isoformat(timespec='seconds')
    if kind == 7:
        if not isinstance(value, bool):
            raise ValueError('Invalid checkbox: ' + field['name'])
        return value
    if kind in (18, 21):
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise ValueError('Invalid relation: ' + field['name'])
        return value
    raise ValueError(f'Unknown field type {kind}: {field["name"]}; review schema before publishing')

def read_base(path):
    envelope = json.loads(path.read_text())
    snapshots = json.loads(gzip.decompress(base64.b64decode(envelope['gzipSnapshot'])))
    tables, schema_digest = {}, []
    for item in snapshots:
        schema = item['schema']; data = schema['data']; table = data['table']
        tid = table['meta']['id']; name = schema['tableMap'][tid]['name']
        fields = table['fieldMap']
        names = [f['name'] for f in fields.values()]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate field names: ' + name)
        records = []
        for rid, values in data['recordMap'].items():
            row = {'源record_id': rid}
            row.update({field['name']: decode(field, (values.get(fid) or {}).get('value'))
                        for fid, field in fields.items()})
            records.append(row)
        if len(records) != table['meta']['recordsNum']:
            raise ValueError('Incomplete export: ' + name)
        records.sort(key=lambda r: r['源record_id'])
        tables[name] = {'table_id': tid, 'rows': records, 'field_names': sorted(names)}
        schema_digest.append({'id': tid, 'fields': fields})
    required = {'00 研究说明与口径', '01 商品规格与价格快照', '02 订单', '03 订单商品明细',
                '04 匿名员工与计薪规则', '05 班次考勤与工时', '06 绩效工资项目', '07 成本与研究事件补录'}
    if set(tables) != required:
        raise ValueError('Source tables changed; review before publishing')
    schema_digest.sort(key=lambda item: item['id'])
    fingerprint = hashlib.sha256(canonical({'tables': tables, 'schemas': schema_digest}).encode()).hexdigest()
    return tables, fingerprint

def clean(path):
    tables, fingerprint = read_base(path)
    issues = []
    def flag(table, row, field, message):
        issues.append({'来源表': table, '源record_id': row['源record_id'], '字段': field,
                       '问题': message, '处理': '保留原值，待核实'})
    cutoffs = [r['数据截至时间'] for t in tables.values() for r in t['rows'] if r.get('数据截至时间')]
    if not cutoffs:
        raise ValueError('Missing source cutoff')
    cutoff = max(cutoffs); end = dt.datetime.fromisoformat(cutoff).date()
    orders = tables['02 订单']['rows']; details = tables['03 订单商品明细']['rows']
    if not orders or not details:
        raise ValueError('Empty order/detail export; keep last successful publication')
    indexes = {name: {r['源record_id']: r for r in table['rows']} for name, table in tables.items()}
    key_fields = {'01 商品规格与价格快照': '规格编号', '02 订单': '匿名订单编号',
                  '03 订单商品明细': '匿名明细编号', '04 匿名员工与计薪规则': '员工规则编号',
                  '05 班次考勤与工时': '匿名班次编号', '06 绩效工资项目': '匿名工资项目编号'}
    for name, key in key_fields.items():
        counts = collections.Counter(r.get(key) for r in tables[name]['rows'])
        for row in tables[name]['rows']:
            if not row.get(key) or counts[row[key]] > 1:
                flag(name, row, key, '缺失或重复业务编号，不去重')
    grouped = collections.defaultdict(list)
    for row in details:
        links = row.get('关联订单') or []
        if len(links) != 1 or links[0] not in indexes['02 订单']:
            flag('03 订单商品明细', row, '关联订单', '订单关联为空、多条或不存在')
            continue
        parent = indexes['02 订单'][links[0]]
        grouped[links[0]].append(row)
        for field, parent_field in [('匿名订单编号', '匿名订单编号'), ('订单已支付', '系统已支付'), ('下单时间', '下单时间')]:
            if row.get(field) != parent.get(parent_field):
                flag('03 订单商品明细', row, field, f'与父订单不一致；明细={row.get(field)}；订单={parent.get(parent_field)}')
    cleaned, daily_source = [], collections.defaultdict(list)
    pairs = [('订单应付总额', '明细应付总额'), ('已退金额', '明细已退金额'),
             ('系统支付净额', '系统支付净额'), ('配送费', '明细配送费')]
    for row in orders:
        paid = row.get('系统已支付'); payment = clean_payment(row)
        net = payment['系统支付净额']; observed = payment['支付观察']
        if observed is None:
            flag('02 订单', row, '系统已支付', '支付状态未知，汇总不可完整计算')
        stamp = row.get('下单时间')
        date = dt.datetime.fromisoformat(stamp).date().isoformat() if stamp else None
        if not date or date != row.get('订单日期'):
            flag('02 订单', row, '订单日期', '下单时间缺失或来源订单日期不一致')
        if date and date > end.isoformat():
            flag('02 订单', row, '下单时间', '下单日期晚于来源数据截至日')
        original_net = amount(row.get('系统支付净额'))
        money_ok = payment['净额核验'] == '一致'
        if not money_ok:
            flag('02 订单', row, '系统支付净额', '来源净额与独立重算差异或缺失')
        related = grouped.get(row['源record_id'], [])
        reconciled = bool(related)
        for ofield, dfield in pairs:
            detail_sum = sum_amount(d.get(dfield) for d in related) if related else None
            order_amount = amount(row.get(ofield))
            if detail_sum is None or order_amount is None or abs(detail_sum-order_amount) > D('.01'):
                reconciled = False
                flag('02 订单', row, ofield, '订单与明细金额差异或不可得')
        result = {'匿名订单编号': row.get('匿名订单编号'), '源record_id': row['源record_id'],
                  '清洗_订单日期': date, '下单时间': stamp, '系统已支付': paid,
                  '测试核实状态': row.get('测试核实状态'), '清洗_支付观察': observed,
                  '订单应付总额': row.get('订单应付总额'), '已退金额': row.get('已退金额'),
                  '来源系统支付净额': row.get('系统支付净额'), '清洗_系统支付净额': scalar(net),
                  '清洗_净额核验': '一致' if money_ok else '待核实',
                  '清洗_订单明细对账': '一致' if reconciled else '待核实',
                  '清洗_销售来源': None if row.get('销售来源') in MISSING else row.get('销售来源'),
                  '原销售来源': row.get('销售来源'),
                  '清洗_顾客类型': None if row.get('顾客标识类型') in MISSING else row.get('顾客标识类型'),
                  '原顾客类型': row.get('顾客标识类型'), '数据截至时间': row.get('数据截至时间')}
        cleaned.append(result)
        if date:
            daily_source[date].append(result)
    daily = []
    start = dt.date.fromisoformat(min(daily_source))
    end = max(end, dt.date.fromisoformat(max(daily_source)))
    for offset in range((end-start).days+1):
        date = start + dt.timedelta(days=offset); rows = daily_source.get(date.isoformat(), [])
        obs = [r for r in rows if r['清洗_支付观察'] is True]
        unknown = any(r['清洗_支付观察'] is None for r in rows)
        daily.append({'日期': date.isoformat(), '订单记录数': len(rows),
                      '支付观察单数': None if not rows or unknown else len(obs),
                      '观察应付_元': scalar(sum_amount(r['订单应付总额'] for r in obs)) if rows and not unknown else None,
                      '累计退款_元': scalar(sum_amount(r['已退金额'] for r in obs)) if rows and not unknown else None,
                      '系统支付净额_元': scalar(sum_amount(r['清洗_系统支付净额'] for r in obs)) if rows and not unknown else None,
                      '数据状态': '来源截至早于订单日期，待核实' if date > dt.datetime.fromisoformat(cutoff).date() else '无订单记录，营业状态未知' if not rows else '部分日，来源截至'+cutoff if date == end else '有记录，营业边界待确认'})
    unknown_paid = sum(r['清洗_支付观察'] is None for r in cleaned)
    summary = {**link_metadata(), '来源数据截至': cutoff,
               '本次读取时间': dt.datetime.now(TZ).isoformat(timespec='seconds'), '来源内容SHA256': fingerprint,
               '各表行数': {name: len(t['rows']) for name,t in tables.items()},
               '订单数': len(orders), '支付状态未判定单数': unknown_paid,
               '支付观察单数': None if unknown_paid else sum(r['清洗_支付观察'] is True for r in cleaned),
               '支付观察应付_元': None if unknown_paid else scalar(sum_amount(r['订单应付总额'] for r in cleaned if r['清洗_支付观察'] is True)),
               '支付观察退款_元': None if unknown_paid else scalar(sum_amount(r['已退金额'] for r in cleaned if r['清洗_支付观察'] is True)),
               '支付观察净额_元': None if unknown_paid else scalar(sum_amount(r['清洗_系统支付净额'] for r in cleaned if r['清洗_支付观察'] is True)),
               '问题条数': len(issues), '问题订单对账数': sum(r['清洗_订单明细对账'] != '一致' for r in cleaned)}
    return {'summary': summary, 'tables': tables, 'clean_orders': cleaned, 'daily': daily, 'issues': issues}

def write_rows(path, rows, delimiter=','):
    if not rows:
        return
    def value(v):
        if v is None: return ''
        if isinstance(v, bool): return 'TRUE' if v else 'FALSE'
        return canonical(v) if isinstance(v, (list,dict)) else v
    with path.open('w', encoding='utf-8-sig' if delimiter == ',' else 'utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerow(rows[0].keys())
        writer.writerows([value(v) for v in row.values()] for row in rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    output = clean(args.input)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir/'clean_result.json').write_text(json.dumps(output, ensure_ascii=False, indent=2))
    for name, rows in [('清洗订单',output['clean_orders']), ('每日数据',output['daily']), ('核查问题',output['issues'])]:
        write_rows(args.output_dir/(name+'.csv'), rows)
        write_rows(args.output_dir/(name+'.tsv'), rows, '\t')
    (args.output_dir/'summary.json').write_text(json.dumps(output['summary'], ensure_ascii=False, indent=2))
    print(json.dumps(output['summary'], ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
