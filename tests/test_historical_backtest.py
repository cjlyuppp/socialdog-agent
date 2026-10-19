from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
import io
import json

import historical_backtest as h


def raw(order='230301000000000001',**updates):
    r={k:'' for k in h.HEADERS}
    r.update({k:'0.00' for k in h.MONEY})
    r.update({'订单编号':order,'创建时间':'2023-03-01 09:00:00','状态':'已完成','类型':'产品',
        '所属分类':'咖啡','收费项目':'美式','规格属性':'中杯','项目单价':'12.00','数量':'1','小计':'12.00',
        '商品退款状态':'无','改价/权益':'不使用','实际成交金额':'12.00','收款金额':'12.00',
        '收款方式':'现金','收款时间':'2023-03-01 09:00:10','配送方式':'到店自提'})
    r.update(updates)
    return r


def history_rows(days=31, cut_day=16, orders_per_day=6):
    """A synthetic month: 美式 drops from 12 to 10 yuan on day cut_day; 拿铁 stays at 16."""
    rows, n = [], 0
    for offset in range(days):
        day = date(2023, 3, 1) + timedelta(days=offset)
        price = '10.00' if day.day >= cut_day else '12.00'
        for k in range(orders_per_day):
            n += 1
            stamp = f'{day.isoformat()} {9 + k:02d}:00:00'
            product, unit = ('美式', price) if k % 2 == 0 else ('拿铁', '16.00')
            rows.append(raw(order=f'2303{n:014d}', 创建时间=stamp, 收款时间=stamp, 收费项目=product,
                            项目单价=unit, 小计=unit, 实际成交金额=unit, 收款金额=unit))
    return rows


def write_history_workbook(path, rows=None):
    import openpyxl
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Sheet1'
    sheet.append(h.HEADERS)
    for r in rows or history_rows():
        sheet.append([r[k] for k in h.HEADERS])
    book.save(path)


class HistoricalBacktestTests(unittest.TestCase):
    def test_explicit_sheet_and_native_excel_dates_build_verified_packages(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'native_dates.xlsx'
            write_history_workbook(source, history_rows(days=1))
            book = openpyxl.load_workbook(source)
            book.active.title = '交易数据'
            book.active['B2'] = datetime(2023, 3, 1, 9)
            book.active['Y2'] = datetime(2023, 3, 1, 9)
            book.save(source)
            book.close()
            with self.assertRaises(ValueError):
                h.build(source, root / 'implicit')
            with redirect_stdout(io.StringIO()):
                h.build(source, root / 'explicit', sheet_name='交易数据')
            self.assertEqual(h.verify(root / 'explicit')['status'], 'passed')
            payload = json.loads((root / 'explicit' / 'workbook_payload.json').read_text())
            self.assertEqual(payload['sheets'][-1]['values'][0][1], '2023-03-01 09:00:00')
            self.assertEqual(h.read_package(root / 'explicit')['data_description']['source_sheet'], '交易数据')

    def test_duplicate_candidates_preserved_whole_order_excluded(self):
        rows=[(2,raw()),(3,raw()),(4,raw(收费项目='拿铁',项目单价='16.00',小计='16.00',实际成交金额='16.00',收款金额='16.00'))]
        lines,orders=h.normalize(rows,'hash')
        self.assertEqual(len(lines),3)
        self.assertEqual(len(orders),1)
        self.assertTrue(all(not r['baseline_eligible'] for r in lines))
        self.assertEqual(sum('duplicate_candidate' in r['flags'] for r in lines),2)

    def test_refund_in_completed_order_excludes_every_line_without_inventing_refund_amount(self):
        lines,orders=h.normalize([(2,raw()),(3,raw(收费项目='拿铁',商品退款状态='退款成功'))],'hash')
        self.assertEqual(orders[0]['status'],'已完成')
        self.assertEqual(orders[0]['baseline_eligible'],0)
        self.assertIsNone(orders[0]['refund_cents'])
        self.assertTrue(all(r['refund_quantity'] is None and r['customer_key'] is None for r in lines))

    def test_payment_year_anomaly_not_corrected_or_used_as_creation_date(self):
        lines,orders=h.normalize([(2,raw(收款时间='2024-07-05 18:00:00'))],'hash')
        self.assertEqual(lines[0]['payment_at'],'2024-07-05 18:00:00')
        self.assertEqual(lines[0]['order_date'],'2023-03-01')
        self.assertIn('payment_year_mismatch',lines[0]['flags'])
        self.assertEqual(lines[0]['baseline_eligible'],1)

    def test_cash_receipt_cannot_prove_payment_for_cancelled_order(self):
        lines,orders=h.normalize([(2,raw(状态='已取消',收款时间=''))],'hash')
        self.assertEqual(lines[0]['recorded_receipt_cents'],1200)
        self.assertEqual(orders[0]['baseline_eligible'],0)
        self.assertIsNone(lines[0]['payment_at'])

    def test_member_price_and_points_are_not_double_subtracted(self):
        r=raw(项目单价='15.00',小计='12.00')
        r.update({'改价/权益':'会员八折','积分抵扣（分摊）':'0.17','收款金额':'11.83'})
        lines,_=h.normalize([(2,r)],'hash')
        self.assertEqual(lines[0]['unit_price_cents'],1500)
        self.assertEqual(lines[0]['recorded_deal_cents'],1200)
        self.assertEqual(lines[0]['recorded_receipt_cents'],1183)
        self.assertEqual(lines[0]['bridge_difference_cents'],0)

    def test_recharge_and_mixed_type_whole_order_excluded(self):
        lines,orders=h.normalize([(2,raw()),(3,raw(类型='充值',收费项目='充值'))],'hash')
        self.assertEqual(len(orders),1)
        self.assertEqual(orders[0]['baseline_eligible'],0)
        self.assertEqual(len(lines),2)

    def test_sql_counts_orders_without_double_counting_line_items(self):
        lines,orders=h.normalize([(2,raw()),(3,raw(收费项目='拿铁')),
            (4,raw(order='230301000000000002',收费项目='美式'))],'hash')
        with tempfile.TemporaryDirectory() as d:
            h.create_db(Path(d)/'history.sqlite',lines,orders)
            result=h.aggregate_sql(Path(d),{'scope':'baseline','by':'all','start_date':None,'end_date':None})
            self.assertEqual(result['rows'][0]['order_count'],2)
            self.assertEqual(result['rows'][0]['line_count'],3)
            self.assertEqual(result['rows'][0]['recorded_receipt_cents'],3600)
            product=h.aggregate_sql(Path(d),{'scope':'baseline','by':'product'})
            self.assertFalse(product['group_order_counts_additive'])
            self.assertEqual(sum(r['order_count'] for r in product['rows']),3)
            absent=h.aggregate_sql(Path(d),{'scope':'baseline','by':'all','start_date':'2024-01-01'})
            self.assertEqual(absent['status'],'unavailable')
            self.assertEqual(absent['rows'],[])

    def test_invalid_dates_sql_and_missing_values_rejected(self):
        for a in [{'scope':'baseline','by':'all','start_date':'2023-02-30'},
                  {'scope':'baseline','by':'all; DROP TABLE lines'},
                  {'scope':'baseline','by':'all','start_date':'2023-03-02','end_date':'2023-03-01'}]:
            with self.assertRaises(ValueError):h.validate_args(a)
        for v in ['',None,'NaN','0.001']:
            with self.assertRaises(ValueError):h.cents(v)
        self.assertEqual(h.cents('0.00'),0)


if __name__=='__main__':unittest.main()
