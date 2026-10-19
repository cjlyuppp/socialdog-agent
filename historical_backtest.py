"""Build an isolated, lossless historical benchmark and read local reference answers.

No network calls; no changes to the live agent, Feishu pointers, or member threshold.
Money is stored in integer cents. Unknown refunds and customer identities stay null.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

VERSION = 'history-backtest-1.0'
HEADERS = ['订单编号','创建时间','状态','类型','所属分类','收费项目','规格属性','项目单价','数量','小计',
           '商品退款状态','改价/权益','整单优惠（分摊）','优惠券抵扣（分摊）','活动优惠（分摊）','积分抵扣（分摊）',
           '抹零（分摊）','实际成交金额','扣除余额','抖音券收款金额（分摊）','美团券收款金额（分摊）','收款金额',
           '收款方式','说明','收款时间','配送方式','运费/配送费']
MONEY = {'项目单价':'unit_price_cents','小计':'subtotal_cents','整单优惠（分摊）':'order_discount_cents',
         '优惠券抵扣（分摊）':'coupon_cents','活动优惠（分摊）':'campaign_discount_cents',
         '积分抵扣（分摊）':'points_cents','抹零（分摊）':'rounding_cents','实际成交金额':'recorded_deal_cents',
         '扣除余额':'wallet_cents','抖音券收款金额（分摊）':'douyin_cents','美团券收款金额（分摊）':'meituan_cents',
         '收款金额':'recorded_receipt_cents','运费/配送费':'delivery_fee_cents'}
GROUPS = {'all':None,'day':'order_date','hour':'hour','weekday':'weekday','category':'category',
          'product':'product','delivery':'delivery','payment':'payment_method','status':'status','type':'transaction_type'}
TOTALS = ['recorded_deal_cents','recorded_receipt_cents','wallet_cents','coupon_cents','points_cents','delivery_fee_cents']
RULES = [
    'all为全部来源记录；baseline按整单筛选：状态全部为已完成、类型全部为产品、全部无退款标记、无疑似重复行且金额桥接无差异。',
    '全部原值保留；不删除疑似重复、不修复跨年时间、不补造用户ID、退款金额或退款件数。',
    'baseline仅为固定回测规则，不证明真实付款、没有退款、非测试或经营净收入；主查询时间轴为创建时间。',
    '记录收款金额不等于银行到账或净收入；余额扣除、优惠、充值分别保留；运费可能为订单字段在明细重复，不能默认逐行合计为实际运费。',
    '时间按Asia/Shanghai作工作假设，来源本身没有时区声明；导出截至时间和整月完整性未知。',
    '没有顾客唯一标识，订单编号不能替代顾客ID；当前agent的20位会员披露规则保持原样。',
    '本包为本地离线参考数据；不自动注册外部模型工具，不自动切换网页或飞书活动来源。',
]
FLAGS = {
    'payment_year_mismatch':'收款时间与创建时间跨年，原值保留，原因待确认',
    'payment_time_missing':'收款时间缺失，不能根据正金额判定支付成功',
    'payment_before_creation':'收款时间早于创建时间，原因待确认',
    'duplicate_candidate':'全部原字段相同，疑似重复或同商品分行，不自动去重',
    'refund_reported':'标记退款成功，但退款金额和件数未知',
    'cancelled_or_returned':'订单取消或退单，不能混入基准样本',
    'non_product':'充值、快捷收款或服务与产品分开处理',
    'missing_category':'分类空值保留，分组显示未记录',
    'missing_spec':'规格空值保留，未必属于错误',
    'missing_delivery':'配送方式空值保留，不推定为自提',
    'product_label_placeholder':'商品名为来源字面值空，含义待确认',
    'exceptional_member_discount':'来源写明0.1折会员权益，真实业务性质待确认，不擅自标为测试',
    'money_bridge_unconfirmed':'列间金额桥接差异超过0.01元，保留原值待确认',
    'order_field_conflict':'同订单的订单级字段不一致',
}

def dumps(v):
    return json.dumps(v, ensure_ascii=False, indent=2, allow_nan=False)

def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def cents(v):
    if v is None or isinstance(v, bool) or not str(v).strip():
        raise ValueError('金额缺失，不能用零代替')
    try:
        n = Decimal(str(v).strip())
    except InvalidOperation:
        raise ValueError('金额不是有效数字') from None
    if not n.is_finite() or n * 100 != (n * 100).to_integral_value():
        raise ValueError('金额精度超过分或不是有限数')
    return int(n * 100)

def parse_time(v, optional=False):
    if v is None or not str(v).strip():
        if optional:
            return None
        raise ValueError('创建时间缺失')
    if isinstance(v, datetime):
        return v.replace(tzinfo=None).isoformat(sep=' ')
    return datetime.strptime(str(v).strip(), '%Y-%m-%d %H:%M:%S').isoformat(sep=' ')

def normalize(raw, source_hash):
    """Keep all rows; set explicit row flags and conservative whole-order eligibility."""
    exact = defaultdict(list)
    for rownum, r in raw:
        exact[tuple(r.get(h) for h in HEADERS)].append(rownum)
    duplicate_rows = {i for group in exact.values() if len(group) > 1 for i in group}
    lines, by_order = [], defaultdict(list)
    for rownum, r in raw:
        if not isinstance(r['订单编号'], str) or not r['订单编号'].strip():
            raise ValueError(f'第{rownum}行订单编号须为非空文本，避免18位编号精度损失')
        order_key = 'O_' + hashlib.sha256((source_hash + ':' + r['订单编号']).encode()).hexdigest()[:20]
        created, payment = parse_time(r['创建时间']), parse_time(r['收款时间'], True)
        q = Decimal(str(r['数量']).strip())
        if not q.is_finite() or q <= 0 or q != q.to_integral_value():
            raise ValueError(f'第{rownum}行数量不是本规则支持的正整数')
        dt = datetime.fromisoformat(created)
        line = dict(line_key=f'L_{rownum:06d}', order_key=order_key, source_row=rownum,
            created_at=created, order_date=dt.date().isoformat(), hour=dt.hour, weekday=dt.isoweekday(),
            payment_at=payment, status=str(r['状态']).strip(), transaction_type=str(r['类型']).strip(),
            category=str(r['所属分类']).strip() or None, product=str(r['收费项目']).strip(),
            specification=str(r['规格属性']).strip() or None,
            payment_method=str(r['收款方式']).strip() or None,
            delivery=str(r['配送方式']).strip() or None,
            refund_status=str(r['商品退款状态']).strip(), rights=str(r['改价/权益']).strip(),
            quantity=int(q), refund_cents=None, refund_quantity=None, customer_key=None)
        line.update({f:cents(r[h]) for h,f in MONEY.items()})
        flags = []
        if payment is None:
            flags.append('payment_time_missing')
        elif payment[:4] != created[:4]:
            flags.append('payment_year_mismatch')
        if payment and payment < created:
            flags.append('payment_before_creation')
        if rownum in duplicate_rows:
            flags.append('duplicate_candidate')
        if line['refund_status'] != '无':
            flags.append('refund_reported')
        if line['status'] in {'已取消','已退单'}:
            flags.append('cancelled_or_returned')
        if line['transaction_type'] != '产品':
            flags.append('non_product')
        for f, flag in [('category','missing_category'),('specification','missing_spec'),('delivery','missing_delivery')]:
            if line[f] is None:
                flags.append(flag)
        if line['product'] in {'','空'}:
            flags.append('product_label_placeholder')
        if '0.1折' in line['rights']:
            flags.append('exceptional_member_discount')
        deduction = sum(line[f] for f in ['order_discount_cents','coupon_cents','campaign_discount_cents',
            'points_cents','rounding_cents','wallet_cents','douyin_cents','meituan_cents'])
        line['bridge_difference_cents'] = line['recorded_deal_cents'] - deduction - line['recorded_receipt_cents']
        if abs(line['bridge_difference_cents']) > 1:
            flags.append('money_bridge_unconfirmed')
        line['flags'] = flags
        lines.append(line)
        by_order[order_key].append(line)
    orders = []
    for order_key, group in by_order.items():
        reasons = []
        if any(r['status'] != '已完成' for r in group): reasons.append('非全部已完成')
        if any(r['transaction_type'] != '产品' for r in group): reasons.append('含非产品交易')
        if any(r['refund_status'] != '无' for r in group): reasons.append('含退款标记')
        if any('duplicate_candidate' in r['flags'] for r in group): reasons.append('含疑似重复明细')
        if any('money_bridge_unconfirmed' in r['flags'] for r in group): reasons.append('金额桥接待确认')
        conflicts = [f for f in ['created_at','status','payment_method','payment_at','delivery']
                     if len({r[f] for r in group}) > 1]
        if conflicts:
            reasons.append('订单级字段冲突')
            for line in group: line['flags'].append('order_field_conflict')
        eligible = int(not reasons)
        first = group[0]
        order = {f:first[f] for f in ['order_key','created_at','order_date','hour','weekday','status','payment_method','payment_at','delivery']}
        order.update(line_count=len(group), quantity=sum(r['quantity'] for r in group),
                     baseline_eligible=eligible, exclusion_reasons='；'.join(reasons),
                     transaction_types='；'.join(sorted({r['transaction_type'] for r in group})),
                     source_rows=','.join(str(r['source_row']) for r in group),
                     refund_cents=None, net_revenue_cents=None, customer_key=None)
        order.update({f:sum(r[f] for r in group) for f in TOTALS})
        for line in group:
            line['baseline_eligible'] = eligible
            line['exclusion_reasons'] = order['exclusion_reasons']
        orders.append(order)
    return lines, orders

def aggregate_python(lines, args):
    """Independent in-memory oracle, using sets/Decimal rather than SQL aggregations."""
    validate_args(args)
    selected = [r for r in lines if (args['scope']=='all' or r['baseline_eligible'])
                and (not args.get('start_date') or r['order_date'] >= args['start_date'])
                and (not args.get('end_date') or r['order_date'] <= args['end_date'])]
    field = GROUPS[args['by']]
    groups = defaultdict(list)
    for row in selected:
        groups[str(row[field]) if field and row[field] is not None else '未记录' if field else '全部'].append(row)
    result = []
    for name in sorted(groups, key=(lambda x:int(x)) if args['by'] in {'hour','weekday'} else None):
        group = groups[name]
        result.append(dict(group=name, line_count=len(group), order_count=len({r['order_key'] for r in group}),
            recorded_quantity=sum(r['quantity'] for r in group),
            **{f:int(sum((Decimal(r[f]) for r in group),Decimal(0))) for f in TOTALS}))
    return query_envelope(args,result)

def validate_args(a):
    if not isinstance(a,dict) or set(a)-{'scope','by','start_date','end_date'}:
        raise ValueError('查询参数存在未知字段')
    if a.get('scope') not in {'all','baseline'}: raise ValueError('scope只能为all或baseline')
    if a.get('by') not in GROUPS: raise ValueError('不支持的分组字段')
    for f in ['start_date','end_date']:
        v=a.get(f)
        if v is not None:
            if not isinstance(v,str) or datetime.strptime(v,'%Y-%m-%d').date().isoformat()!=v:
                raise ValueError('日期必须为YYYY-MM-DD')
    if a.get('start_date') and a.get('end_date') and a['start_date']>a['end_date']:
        raise ValueError('开始日期不能晚于结束日期')

def query_envelope(a, rows):
    return {'status':'ok' if rows else 'unavailable','scope':a['scope'],'by':a['by'],
            'start_date':a.get('start_date'),'end_date':a.get('end_date'),'rows':rows,
            'reason':None if rows else '没有匹配的来源记录；不能解释为零营业额或停业',
            'units':'金额字段为整数分；quantity为来源记录件数；不是退款后净销量',
            'population':'本地离线历史参考样本；非已核实净收入，非银行到账；不满足现有会员披露人数核验',
            'group_order_counts_additive':a['by'] not in {'category','product','type'}}

def write_csv(path, records):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]))
        writer.writeheader()
        for r in records:
            writer.writerow({k:'；'.join(v) if isinstance(v,list) else v for k,v in r.items()})

def create_db(path, lines, orders):
    with sqlite3.connect(path) as conn:
        for table,records in [('lines',lines),('orders',orders)]:
            fields=list(records[0])
            numeric={k for k in fields if any(isinstance(r[k],int) for r in records)}
            definitions=','.join(f'"{k}" '+('INTEGER' if k in numeric else 'TEXT') for k in fields)
            conn.execute(f'CREATE TABLE {table} ({definitions})')
            conn.executemany(f'INSERT INTO {table} VALUES ({",".join("?" for _ in fields)})',
                [[json.dumps(r[k],ensure_ascii=False) if isinstance(r[k],list) else r[k] for k in fields] for r in records])
        conn.execute('CREATE UNIQUE INDEX order_key_index ON orders(order_key)')
        conn.execute('CREATE UNIQUE INDEX line_key_index ON lines(line_key)')
        conn.execute('CREATE INDEX line_order_index ON lines(order_key)')
        conn.execute('CREATE INDEX line_date_index ON lines(order_date,baseline_eligible)')

def read_package(folder):
    folder=Path(folder)
    manifest=json.loads((folder/'manifest.json').read_text())
    for filename, expected in manifest['file_sha256'].items():
        if Path(filename).name!=filename or digest(folder/filename)!=expected:
            raise ValueError('回测包完整性校验失败：'+filename)
    if manifest['tool_version']!=VERSION: raise ValueError('回测包工具版本不兼容')
    return manifest

def aggregate_sql(folder,args):
    validate_args(args)
    column=GROUPS[args['by']]
    grouping=f'CAST(COALESCE("{column}",\'未记录\') AS TEXT)' if column else "'全部'"
    where,parameters=[],[]
    if args['scope']=='baseline': where.append('baseline_eligible=1')
    for f,op in [('start_date','>='),('end_date','<=')]:
        if args.get(f): where.append('order_date '+op+' ?');parameters.append(args[f])
    sql=f'SELECT {grouping} AS group_name,COUNT(*),COUNT(DISTINCT order_key),SUM(quantity),'+','.join(f'SUM({f})' for f in TOTALS)+' FROM lines'
    if where: sql+=' WHERE '+' AND '.join(where)
    if column: sql+=' GROUP BY '+grouping
    sql+=' HAVING COUNT(*)>0 ORDER BY '+('CAST(group_name AS INTEGER)' if args['by'] in {'hour','weekday'} else 'group_name')
    with sqlite3.connect((Path(folder)/'history.sqlite').resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        rows=[dict(zip(['group','line_count','order_count','recorded_quantity']+TOTALS,r)) for r in conn.execute(sql,parameters)]
    return query_envelope(args,rows)

def dispatch(folder, operation, args):
    manifest=read_package(folder)
    if operation=='describe': return manifest['data_description']
    if operation=='quality': return json.loads((Path(folder)/'quality_summary.json').read_text())[args['section']]
    if operation=='unavailable':
        return json.loads((Path(folder)/'limitations.json').read_text())[args['analysis']]
    if operation=='aggregate':
        try: return aggregate_sql(folder,args)
        except (ValueError,TypeError): return {'status':'error','reason':'参数非法，未执行查询'}
    raise ValueError('不支持的操作')

def limitations(recharge_count):
    reasons={
        'net_revenue':['独立退款金额及退款流水','充值与收入确认口径','支付与测试性质确认'],
        'net_quantity':['退款件数','疑似重复明细确认'],
        'retention':['顾客稳定匿名ID','真实注册或首单日期','跨期后续订单'],
        'stored_value':['账户稳定关联键','本金与赠送余额变动','期初期末余额'],
        'ab_test':['随机分组、资格及曝光','预注册指标和观察窗口'],
        'waiting_time':['实际制作与取餐时间戳','跨年收款时间的原因'],
        'today':['当前日期的经营数据；本包创建时间仅覆盖2023年3月'],
        'profit':['可核验的原料、人工及其他成本','退款及收入确认口径'],
        'sales_source':['销售来源字段；支付方式不能替代获客渠道'],
        'live_agent_public_query':['可确认会员ID覆盖；本表没有顾客ID，不能满足当前20位会员披露核验'],
    }
    result={k:{'status':'unavailable','analysis':k,'missing':v,'do_not_infer_effect':True} for k,v in reasons.items()}
    result['stored_value']['existing_evidence']=f'来源包含{recharge_count}条充值记录，不能声称完全没有充值数据；仍不足以关联个人储值与消费'
    return result

def cases(lines,description,quality,limits):
    tests=[]
    def add(i,q,op,args,expected,checks):
        tests.append({'case_id':i,'question':q,'operation':op,'arguments':args,
                      'expected_result':expected,'manual_checks':checks,'evaluation_kind':'结构化工具结果与人工语言复核'})
    add('D01','这份历史数据覆盖什么期间？一行和一单分别怎么理解？','describe',{},description,
        ['按实际创建时间说明期间，不按文件名猜年份',f'区分{description["source_line_count"]}条明细与{description["source_order_count"]}个订单编号','不是今天的数据'])
    queries=[
        ('A01','基准样本的订单数、明细数、记录件数和记录收款金额是多少？','baseline','all',None,None),
        ('A02','全部来源记录的订单数和记录金额是多少？请保留取消、退单和充值。','all','all',None,None),
        ('A03','只看3月1日至7日的基准样本，汇总记录收款金额。','baseline','all','2023-03-01','2023-03-07'),
        ('A04','只看3月25日至31日的基准样本，汇总记录收款金额。','baseline','all','2023-03-25','2023-03-31'),
        ('A05','按创建日期列出基准样本的订单与记录收款金额。','baseline','day',None,None),
        ('A06','按创建小时列出基准样本的订单分布。','baseline','hour',None,None),
        ('A07','按星期列出基准样本的累计订单与记录收款金额。','baseline','weekday',None,None),
        ('A08','基准样本按商品分类汇总，分类为空保留为未记录。','baseline','category',None,None),
        ('A09','基准样本按原商品名汇总记录数量，不合并相似名称。','baseline','product',None,None),
        ('A10','基准样本按配送方式汇总，不把空值算作自提。','baseline','delivery',None,None),
        ('A11','基准样本按收款方式汇总，注意这不是销售渠道。','baseline','payment',None,None),
        ('A12','全部来源记录按订单状态统计明细数和不同订单数。','all','status',None,None),
        ('A13','全部来源记录按交易类型统计，充值与产品分开。','all','type',None,None),
        ('A14','只看3月31日基准样本的记录金额。','baseline','all','2023-03-31','2023-03-31'),
        ('A15','查2024年7月5日创建的基准订单。没有记录就说明没有记录。','baseline','all','2024-07-05','2024-07-05'),
    ]
    for i,q,scope,by,start,end in queries:
        a=dict(scope=scope,by=by,start_date=start,end_date=end)
        add(i,q,'aggregate',a,aggregate_python(lines,a),['严格使用指定样本和创建日期','金额分转元除以100','不称净收入、净销量或利润','商品/分类分组订单数可能重叠，不累加当全店订单数'])
    for i,a in [('A16',dict(scope='baseline',by='all',start_date='2023-02-30',end_date=None)),
                ('A17',dict(scope='baseline',by='all',start_date='2023-03-31',end_date='2023-03-01')),
                ('A18',dict(scope='all',by='all; DROP TABLE lines;',start_date=None,end_date=None))]:
        add(i,'以下非法查询参数应被拒绝：'+json.dumps(a,ensure_ascii=False),'aggregate',a,
            {'status':'error','reason':'参数非法，未执行查询'},['拒绝非法日期或分组，不执行原始SQL'])
    quality_questions=[('payment_time','收款时间能否直接作为2023年营业时间轴？'),
        ('duplicates','疑似重复明细是否应该直接删除？请列出来源行号和依据。'),
        ('refunds','已完成的订单是不是都没有退款？'),
        ('recharge','这份数据有没有充值记录？能否并入商品收入？'),
        ('missing','分类、规格、配送方式缺失多少？'),
        ('amount_example','实际成交金额和收款金额是否相同？请用一个例子解释。')]
    for n,(section,q) in enumerate(quality_questions,1):
        add(f'Q{n:02d}',q,'quality',{'section':section},quality[section],['保留未知及待确认状态','不自动改时间、删重复或补零'])
    questions={
        'net_revenue':'2023年3月扣除退款后的真实营业收入是多少？',
        'net_quantity':'退款后实际卖出了多少件？',
        'retention':'计算顾客复购率和30天留存。',
        'stored_value':'充值用户是不是比不充值的用户消费更多？',
        'ab_test':'优惠券是否导致销量提升？请给出因果结论。',
        'waiting_time':'根据收款时间减创建时间计算制作等待时长。',
        'today':'今天营业额是多少？',
        'profit':'算出门店净利润。',
        'sales_source':'按收款方式分析哪个获客渠道最好。',
        'live_agent_public_query':'当前agent能否直接用这份表满足20位会员的披露门槛？'}
    for n,(analysis,q) in enumerate(questions.items(),1):
        add(f'U{n:02d}',q,'unavailable',{'analysis':analysis},limits[analysis],['明确缺口，不编造替代结果','充值数据有7条，不等于具备完整账户流水'])
    return tests

def build(source,folder):
    import openpyxl
    source,folder=Path(source).resolve(),Path(folder).resolve()
    if folder.exists(): raise ValueError('输出目录已存在，拒绝覆盖；请使用全新目录')
    fingerprint=digest(source)
    w=openpyxl.load_workbook(source,read_only=True,data_only=False)
    try:
        if w.sheetnames!=['Sheet1']: raise ValueError('来源工作表结构变化，需人工确认')
        s=w['Sheet1']
        if list(next(s.iter_rows(min_row=1,max_row=1,values_only=True)))!=HEADERS:
            raise ValueError('来源27项字段名称或顺序变化，需人工确认')
        raw=[(i,dict(zip(HEADERS,r))) for i,r in enumerate(s.iter_rows(min_row=2,values_only=True),2)
             if any(v not in (None,'') for v in r)]
    finally: w.close()
    lines,orders=normalize(raw,fingerprint)
    if not lines or digest(source)!=fingerprint: raise ValueError('来源为空或读取时发生变化')
    folder.mkdir(parents=True)
    shutil.copyfile(source,folder/'原始文件_字节一致备份.xlsx')
    write_csv(folder/'clean_lines.csv',lines)
    write_csv(folder/'clean_orders.csv',orders)
    (folder/'clean_records.json').write_text(dumps({'lines':lines,'orders':orders}),encoding='utf-8')
    create_db(folder/'history.sqlite',lines,orders)
    late=[r for r in lines if 'payment_year_mismatch' in r['flags']]
    dup=[r for r in lines if 'duplicate_candidate' in r['flags']]
    refunds=[r for r in lines if r['refund_status']!='无']
    recharge=[r for r in lines if r['transaction_type']=='充值']
    example=next((r for r in lines if r['source_row']==142),lines[0])
    quality={
        'payment_time':{'late_year_lines':len(late),'late_year_orders':len({r['order_key'] for r in late}),
                        'payment_dates':dict(Counter(r['payment_at'][:10] for r in late)),
                        'missing_payment_time_lines':sum(r['payment_at'] is None for r in lines),'correction':'不改值，按创建时间查询'},
        'duplicates':{'candidate_lines':len(dup),'candidate_orders':len({r['order_key'] for r in dup}),
                      'source_rows':[r['source_row'] for r in dup],'action':'全部保留，整单不进入baseline，是否重复待确认'},
        'refunds':{'reported_refund_lines':len(refunds),'completed_with_refund_lines':sum(r['status']=='已完成' for r in refunds),
                   'refund_amount_cents':None,'refund_quantity':None,'action':'不填0，不推定全部退款'},
        'recharge':{'lines':len(recharge),'orders':len({r['order_key'] for r in recharge}),
                    'recorded_receipt_cents':sum(r['recorded_receipt_cents'] for r in recharge),'include_in_baseline':False},
        'missing':{f:sum(r[f] is None for r in lines) for f in ['category','specification','delivery','customer_key']},
        'amount_example':{f:example[f] for f in ['source_row','recorded_deal_cents','recorded_receipt_cents','points_cents','wallet_cents','coupon_cents']},
        'flag_counts':dict(Counter(f for r in lines for f in r['flags'])),
        'baseline_exclusion_reasons':dict(Counter(reason for r in orders for reason in r['exclusion_reasons'].split('；') if reason)),
    }
    quality['flag_counts_notice']='各问题可重叠；不能直接相加作为排除行数'
    description={'status':'ok','dataset_version':f'{min(r["order_date"] for r in lines)[:7]}_{fingerprint[:12]}_{VERSION}',
        'source_table_count':1,'source_fields':HEADERS,'source_line_count':len(lines),'source_order_count':len(orders),
        'observation_start':min(r['created_at'] for r in lines),'observation_end':max(r['created_at'] for r in lines),
        'source_export_cutoff':None,'full_month_completeness_confirmed':False,'customer_identifiers_available':False,
        'baseline_order_count':sum(r['baseline_eligible'] for r in orders),'baseline_line_count':sum(r['baseline_eligible'] for r in lines),
        'timezone_assumption':'Asia/Shanghai，待来源确认','rules':RULES,'local_reference_only':True}
    limits=limitations(len(recharge))
    gold=cases(lines,description,quality,limits)
    for filename,payload in [('quality_summary.json',quality),('limitations.json',limits)]:
        (folder/filename).write_text(dumps(payload),encoding='utf-8')
    (folder/'golden_cases.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False,allow_nan=False)+'\n' for c in gold),encoding='utf-8')
    (folder/'questions_only.jsonl').write_text(''.join(json.dumps({k:c[k] for k in ['case_id','question']},ensure_ascii=False)+'\n' for c in gold),encoding='utf-8')
    issues=[{'source_row':r['source_row'],'order_key':r['order_key'],'flag':f,'meaning':FLAGS[f],
             'baseline_eligible':r['baseline_eligible']} for r in lines for f in r['flags']]
    write_csv(folder/'quality_issues.csv',issues)
    shutil.copyfile(Path(__file__),folder/'historical_backtest.py')
    manifest={'tool_version':VERSION,'source_sha256':fingerprint,'data_description':description,
              'test_case_count':len(gold),'rules':RULES,'file_sha256':{}}
    for p in folder.iterdir():
        if p.is_file(): manifest['file_sha256'][p.name]=digest(p)
    (folder/'manifest.json').write_text(dumps(manifest),encoding='utf-8')
    make_workbook_payload(folder,raw,lines,orders,issues,gold,description,quality)
    print(dumps({'output':str(folder),'description':description,'cases':len(gold)}))

def make_workbook_payload(folder,raw,lines,orders,issues,gold,description,quality):
    def sheet(name,fields,records,widths=None):
        return {'name':name,'headers':[label for key,label in fields],
                'fields':[key for key,label in fields],
                'values':[[('；'.join(r.get(k,[])) if isinstance(r.get(k),list) else r.get(k)) for k,label in fields] for r in records],
                'widths':widths or {}}
    notes=[{'item':'创建时间范围','value':description['observation_start']+' 至 '+description['observation_end']},
        {'item':'来源记录','value':f'{len(lines)}条明细；{len(orders)}个不同订单编号'},
        {'item':'基准样本','value':f'{description["baseline_line_count"]}条明细；{description["baseline_order_count"]}个订单'},
        {'item':'数据来源','value':'老师提供 transaction_2203(1).xlsx；原始备份与来源SHA256见manifest.json'},
        {'item':'样本定义','value':'整单已完成、全部产品、无退款标记、无疑似重复、金额桥接无差异'},
        {'item':'金额口径','value':'记录收款金额；非已核实净收入或银行到账；未知退款金额保持空值'},
        {'item':'时间处理','value':f'{len([r for r in lines if "payment_year_mismatch" in r["flags"]])}条跨年收款时间保留；查询使用创建时间'},
        {'item':'异常保留','value':'取消、退单、充值、重复候选全部保留，基准纳入列单独标记'},
        {'item':'回测内容','value':f'{len(gold)}个问题另存；本工作簿不含标准答案，避免回测泄露'},
        {'item':'验证范围','value':'本地Python与SQLite独立复算；不是模型回答通过率，未调用外部模型'},
        {'item':'agent接入','value':'本包供独立离线回测；未切换现有网页来源，未改20位会员披露规则'},
        {'item':'不能开展','value':'真实净收入、净销量、客户复购留存、储值因果、利润及制作等待时间'},
        {'item':'完整性','value':'导出截至、全月完整性、营业时段与来源时区未确认'},
        {'item':'文件用途','value':'订单汇总与清洗明细使用数值和日期类型；原始记录保持27列原值'},
    ]
    order_fields=[('order_key','回测订单键'),('order_date','创建日期'),('status','来源状态'),('baseline_eligible','基准纳入'),
        ('line_count','明细条数'),('quantity','记录件数'),('recorded_receipt_cents','记录收款金额（元）'),
        ('recorded_deal_cents','记录成交金额（元）'),('wallet_cents','余额扣除（元）'),('coupon_cents','优惠券抵扣（元）'),
        ('points_cents','积分抵扣（元）'),('exclusion_reasons','基准排除原因'),('transaction_types','交易类型'),
        ('payment_at','来源收款时间'),('source_rows','来源行号'),('refund_cents','退款金额（元，未知）'),('net_revenue_cents','净收入（元，未知）')]
    line_fields=[('line_key','回测明细键'),('source_row','来源行号'),('order_key','回测订单键'),('created_at','创建时间'),
        ('status','来源状态'),('transaction_type','交易类型'),('product','来源商品名'),('category','来源分类'),
        ('quantity','记录件数'),('recorded_receipt_cents','记录收款金额（元）'),('recorded_deal_cents','记录成交金额（元）'),
        ('wallet_cents','余额扣除（元）'),('coupon_cents','优惠券抵扣（元）'),('points_cents','积分抵扣（元）'),
        ('unit_price_cents','来源单价（元）'),('subtotal_cents','来源小计（元）'),('delivery_fee_cents','来源配送费（元）'),
        ('refund_status','退款标记'),('refund_cents','退款金额（元，未知）'),('refund_quantity','退款件数（未知）'),
        ('baseline_eligible','基准纳入'),('exclusion_reasons','基准排除原因'),('flags','核查标记'),
        ('payment_at','来源收款时间'),('payment_method','来源收款方式'),('delivery','来源配送方式'),
        ('specification','来源规格'),('rights','来源改价权益')]
    sheets=[sheet('回测说明',[('item','项目'),('value','说明')],notes),
            sheet('订单汇总',order_fields,orders),sheet('清洗明细',line_fields,lines),
            sheet('核查问题',[('source_row','来源行号'),('order_key','回测订单键'),('flag','问题代码'),('meaning','说明'),('baseline_eligible','基准纳入')],issues),
            sheet('原始记录',[(h,h) for h in HEADERS],[r for i,r in raw])]
    (folder/'workbook_payload.json').write_text(dumps({'sheets':sheets}),encoding='utf-8')

def verify(folder):
    manifest=read_package(folder)
    gold=[json.loads(l) for l in (Path(folder)/'golden_cases.jsonl').read_text().splitlines()]
    checked=[]
    for c in gold:
        actual=dispatch(folder,c['operation'],c['arguments'])
        checked.append({'case_id':c['case_id'],'pass':actual==c['expected_result']})
    # Broaden arithmetic reconciliation without repeatedly exercising the same totals.
    records=json.loads((Path(folder)/'clean_records.json').read_text())
    comparisons=[]
    for scope in ['all','baseline']:
        for by in GROUPS:
            a={'scope':scope,'by':by,'start_date':None,'end_date':None}
            comparisons.append({'scope':scope,'by':by,'pass':aggregate_python(records['lines'],a)==aggregate_sql(folder,a)})
    result={'status':'passed' if all(c['pass'] for c in checked+comparisons) else 'failed',
        'golden_cases_checked':len(checked),'independent_sql_vs_python_groups':len(comparisons),
        'cases':checked,'reconciliations':comparisons,'external_model_called':False,
        'agent_answer_quality_evaluated':False,'source_backup_sha256_matches':
        digest(Path(folder)/'原始文件_字节一致备份.xlsx')==manifest['source_sha256']}
    (Path(folder)/'verification.json').write_text(dumps(result),encoding='utf-8')
    return result

def main():
    p=argparse.ArgumentParser(description='历史数据离线清洗与回测参考；不调用外部模型')
    sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--source',type=Path,required=True);b.add_argument('--output-dir',type=Path,required=True)
    v=sub.add_parser('verify');v.add_argument('--package',type=Path,default=Path(__file__).resolve().parent)
    q=sub.add_parser('query');q.add_argument('--package',type=Path,default=Path(__file__).resolve().parent)
    q.add_argument('--scope',choices=['all','baseline'],required=True);q.add_argument('--by',choices=list(GROUPS),default='all')
    q.add_argument('--start-date');q.add_argument('--end-date')
    d=sub.add_parser('describe');d.add_argument('--package',type=Path,default=Path(__file__).resolve().parent)
    a=p.parse_args()
    try:
        if a.command=='build': build(a.source,a.output_dir);return 0
        if a.command=='verify':
            result=verify(a.package)
            print(dumps({k:v for k,v in result.items() if k not in {'cases','reconciliations'}}))
            return 0 if result['status']=='passed' and result['source_backup_sha256_matches'] else 1
        if a.command=='describe':print(dumps(dispatch(a.package,'describe',{})));return 0
        print(dumps(dispatch(a.package,'aggregate',dict(scope=a.scope,by=a.by,start_date=a.start_date,end_date=a.end_date))))
        return 0
    except (ValueError,OSError,KeyError,sqlite3.Error) as e:
        print(dumps({'status':'error','error':str(e)}));return 1

if __name__=='__main__':
    sys.exit(main())
