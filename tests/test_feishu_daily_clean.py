import copy, unittest
from feishu_daily_clean import amount, clean_payment, decode, clean
from unittest.mock import patch
from decimal import Decimal
from pathlib import Path

class PaymentTest(unittest.TestCase):
    def row(self, **fields):
        row = {'源record_id':'record-1', '系统已支付':True, '订单应付总额':12.30,
               '已退金额':2.30, '系统支付净额':10, '测试核实状态':'待店方确认'}
        row.update(fields)
        return row

    def test_decimal_refund_and_zero(self):
        result = clean_payment(self.row())
        self.assertEqual(result['系统支付净额'],10)
        self.assertEqual(result['净额核验'],'一致')
        zero = clean_payment(self.row(订单应付总额=0,已退金额=0,系统支付净额=0))
        self.assertEqual(zero['系统支付净额'],0)
        self.assertTrue(zero['支付观察'])

    def test_missing_is_not_zero(self):
        result = clean_payment(self.row(已退金额=None))
        self.assertIsNone(result['系统支付净额'])
        self.assertEqual(result['净额核验'],'待核实')
        self.assertIsNone(amount(True))

    def test_unpaid_unknown_and_test(self):
        self.assertEqual(clean_payment(self.row(系统已支付=False,系统支付净额=0))['系统支付净额'],0)
        self.assertIsNone(clean_payment(self.row(系统已支付=None))['支付观察'])
        self.assertFalse(clean_payment(self.row(测试核实状态='明确测试标记'))['支付观察'])

    def test_source_unchanged_and_stable_record(self):
        row = self.row(); before = copy.deepcopy(row)
        self.assertEqual(clean_payment(row)['源record_id'],row['源record_id'])
        self.assertEqual(row,before)

    def test_id_zero_date_and_schema_change(self):
        self.assertEqual(decode({'name':'规格编号','type':1},[{'text':'0'}]),'0')
        self.assertEqual(decode({'name':'日期','type':5},1791621493000),'2026-10-10T16:38:13+08:00')
        with self.assertRaises(ValueError):
            decode({'name':'newtype','type':999},42)

    def test_new_day_extends_and_empty_day_is_unknown(self):
        row = self.row(下单时间='2026-10-10T11:00:00+08:00',订单日期='2026-10-10',配送费=0,
                       数据截至时间='2026-10-12T12:00:00+08:00')
        new = self.row(源record_id='record-2',下单时间='2026-10-12T11:00:00+08:00',订单日期='2026-10-12',配送费=0,
                       数据截至时间='2026-10-12T12:00:00+08:00')
        tables = {'02 订单':{'rows':[row,new]},'03 订单商品明细':{'rows':[]}}
        for name in ['01 商品规格与价格快照','04 匿名员工与计薪规则','05 班次考勤与工时','06 绩效工资项目']:
            tables[name] = {'rows':[]}
        with patch('feishu_daily_clean.read_base',return_value=(tables,'digest')):
            with self.assertRaises(ValueError):
                clean(Path('unused'))
        tables['03 订单商品明细']['rows'] = [
            {'源record_id':'detail-'+r['源record_id'],'匿名明细编号':'detail-'+r['源record_id'],
             '关联订单':[r['源record_id']],'下单时间':r['下单时间'],'订单已支付':True,
             '明细应付总额':12.30,'明细已退金额':2.30,'系统支付净额':10,'明细配送费':0}
            for r in [row,new]]
        with patch('feishu_daily_clean.read_base',return_value=(tables,'digest')):
            result = clean(Path('unused'))
        self.assertEqual(len(result['daily']),3)
        self.assertIsNone(result['daily'][1]['系统支付净额_元'])
        self.assertEqual(result['daily'][2]['系统支付净额_元'],10)

if __name__ == '__main__': unittest.main()
