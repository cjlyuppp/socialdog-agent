from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import eval_runner
import historical_backtest as h
from socialdog_agent import ResearchAgent, build_history_tools
from socialdog_agent.grounding import mentions, ungrounded_numbers
from socialdog_agent.history_tools import to_yuan
from test_chat_agent import ScriptedLLM, call, message, response
from test_historical_backtest import history_rows, write_history_workbook


class HistoryPackageMixin:
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        source = cls.root / 'history.xlsx'
        write_history_workbook(source)
        with redirect_stdout(io.StringIO()):  # build prints its summary
            h.build(source, cls.root / 'package')
            h.build(source, cls.root / 'cut', as_of='2023-03-20')
        cls.package = cls.root / 'package'
        cls.cases = [json.loads(l) for l in (cls.package / 'golden_cases.jsonl').read_text().splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tools(self, package=None, min_orders=5):
        return build_history_tools(package or self.package, self.root / 'state', min_orders)


class HistoryPackageTest(HistoryPackageMixin, unittest.TestCase):
    def test_full_and_truncated_packages_verify(self):
        self.assertEqual(h.verify(self.package)['status'], 'passed')
        result = h.verify(self.root / 'cut')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['source_backup_sha256_matches'], 'omitted_for_as_of')

    def test_as_of_physically_removes_later_rows(self):
        cut = self.root / 'cut'
        self.assertFalse((cut / '原始文件_字节一致备份.xlsx').exists())
        description = h.dispatch(cut, 'describe', {})
        self.assertEqual(description['as_of'], '2023-03-20')
        self.assertLessEqual(description['observation_end'][:10], '2023-03-20')
        lines = h.load_lines(cut)
        self.assertTrue(lines and max(r['order_date'] for r in lines) <= '2023-03-20')
        for text in [(cut / 'workbook_payload.json').read_text(), (cut / 'clean_records.json').read_text()]:
            self.assertNotIn('2023-03-21', text)
        # Even an explicit request for later dates finds nothing.
        later = self.tools(cut).execute('history_aggregate', json.dumps(
            {'scope': 'all', 'by': 'all', 'start_date': '2023-03-21', 'end_date': None}), 'q')
        self.assertEqual(later['rows'], [])

    def test_as_of_redacts_payment_time_after_cutoff(self):
        rows = [(2, history_rows(days=1)[0])]
        rows[0][1]['收款时间'] = '2023-03-05 10:00:00'
        kept, removed = h.truncate_as_of(rows, '2023-03-01')
        lines, _ = h.normalize(kept, 'hash')
        self.assertEqual(removed, 0)
        self.assertIsNone(lines[0]['payment_at'])
        self.assertIn('payment_time_redacted_after_as_of', lines[0]['flags'])

    def test_price_event_reconstructed_from_unit_price(self):
        result = h.price_events(h.load_lines(self.package))
        self.assertEqual(result['event_count'], 1)
        event = result['events'][0]
        self.assertEqual((event['product'], event['event_date'], event['old_unit_price_cents'],
                          event['new_unit_price_cents'], event['direction']), ('美式', '2023-03-16', 1200, 1000, '降价'))
        self.assertEqual(event['before']['product_quantity'], 42)
        self.assertEqual(event['after']['product_quantity'], 42)
        self.assertEqual(event['change_pct']['product_quantity_per_day'], 0.0)
        self.assertEqual(event['profit']['status'], 'unavailable')

    def test_single_day_price_blip_is_not_an_event(self):
        rows = history_rows(days=10, cut_day=99)
        rows[0]['项目单价'] = '9.00'
        lines, _ = h.normalize(list(enumerate(rows, 2)), 'hash')
        self.assertEqual(h.price_events(lines)['event_count'], 0)

    def test_week_and_month_groups_match_python_oracle(self):
        checked = {(r['scope'], r['by']) for r in h.verify(self.package)['reconciliations'] if r['pass']}
        self.assertTrue({('baseline', 'week'), ('baseline', 'month')} <= checked)
        weeks = h.aggregate_sql(self.package, {'scope': 'baseline', 'by': 'week'})
        self.assertEqual(weeks['rows'][0]['group'], '2023-02-27')


class HistoryToolsTest(HistoryPackageMixin, unittest.TestCase):
    def test_order_threshold_replaces_member_rule_and_hides_small_groups(self):
        result = self.tools(min_orders=4).execute('history_aggregate', json.dumps(
            {'scope': 'baseline', 'by': 'day', 'start_date': '2023-03-01', 'end_date': '2023-03-02'}), 'q')
        self.assertEqual([r['group'] for r in result['rows']], ['2023-03-01', '2023-03-02'])
        hidden = self.tools(min_orders=7).execute('history_aggregate', json.dumps(
            {'scope': 'baseline', 'by': 'day', 'start_date': '2023-03-01', 'end_date': '2023-03-02'}), 'q')
        self.assertEqual(hidden['rows'], [])
        self.assertEqual(hidden['suppressed_group_count'], 2)
        self.assertEqual(hidden['privacy']['minimum_orders_per_group'], 7)

    def test_money_is_returned_in_yuan_with_exposure(self):
        result = self.tools().execute('history_aggregate', json.dumps(
            {'scope': 'baseline', 'by': 'weekday', 'start_date': None, 'end_date': None}), 'q')
        wednesday = next(r for r in result['rows'] if r['group'] == '3')
        self.assertNotIn('recorded_receipt_cents', wednesday)
        self.assertEqual(wednesday['days_in_scope'], 5)  # March 2023 has five Wednesdays.
        self.assertEqual(wednesday['days_with_orders'], 5)
        self.assertEqual(wednesday['orders_per_day_with_orders'], 6.0)
        month = self.tools().execute('history_aggregate', json.dumps(
            {'scope': 'baseline', 'by': 'week', 'start_date': None, 'end_date': None}), 'q')
        self.assertFalse(month['rows'][0]['period_complete'])
        self.assertEqual(month['rows'][0]['days_in_scope'], 5)

    def test_product_filter_and_price_events_tool(self):
        tools = self.tools()
        result = tools.execute('history_aggregate', json.dumps(
            {'scope': 'baseline', 'by': 'all', 'start_date': None, 'end_date': None, 'product': '拿铁'}), 'q')
        self.assertEqual(result['rows'][0]['recorded_quantity'], 93)
        events = tools.execute('history_price_events', json.dumps({'product': '美式'}), 'q')
        self.assertEqual(events['events'][0]['new_unit_price_yuan'], 10.0)
        hidden = self.tools(min_orders=100).execute('history_price_events', json.dumps({'product': None}), 'q')
        self.assertTrue(hidden['events'][0]['before']['suppressed'])
        self.assertIsNone(hidden['events'][0]['change_pct']['product_quantity_per_day'])

    def test_invalid_arguments_and_paths_do_not_reach_model(self):
        tools = self.tools()
        for name, args in [('history_aggregate', {'scope': 'all', 'by': 'all; DROP TABLE lines;',
                                                  'start_date': None, 'end_date': None}),
                           ('history_aggregate', {'scope': 'all', 'by': 'all', 'start_date': '2023-02-30', 'end_date': None}),
                           ('history_quality', {'section': '../../etc'}),
                           ('history_price_events', {'product': None, 'window_days': 0})]:
            with self.subTest(name=name, args=args):
                self.assertEqual(tools.execute(name, json.dumps(args), 'q')['status'], 'error')
        self.assertNotIn(str(self.root), json.dumps(tools.source_info))

    def test_to_yuan_only_renames_cents(self):
        self.assertEqual(to_yuan({'a_cents': 1234, 'b': {'c_cents': None}, 'pct': 1.5}),
                         {'a_yuan': 12.34, 'b': {'c_yuan': None}, 'pct': 1.5})


class GroundingTest(unittest.TestCase):
    evidence = [{'rows': [{'order_count': 186, 'recorded_receipt_yuan': 2544.0, 'pct': -16.67, 'x': 1234.567}]}]

    def test_dates_rounding_units_and_small_numbers_are_accepted(self):
        text = '共186单，收款2,544.00元（约0.25万元），下降16.67%，日均1234.57，2023年3月1日至3月31日，第3周，A01，1. 共3组'
        self.assertEqual(ungrounded_numbers(text, self.evidence), [])

    def test_unsupported_numbers_are_reported(self):
        self.assertEqual(ungrounded_numbers('共187单，增长12%，收款2600元', self.evidence), ['187', '12%', '2600'])

    def test_mentions(self):
        self.assertTrue(mentions('收款2,544元', 2544.0))
        self.assertTrue(mentions('约0.25万元', 2544))
        self.assertFalse(mentions('2544.1', 2544))


class HistoryAgentTest(HistoryPackageMixin, unittest.TestCase):
    def agent(self, llm):
        return ResearchAgent(llm, self.tools(), self.root / 'state')

    def test_history_mode_instructions_and_answer_versions(self):
        args = {'scope': 'baseline', 'by': 'all', 'start_date': None, 'end_date': None}
        llm = ScriptedLLM(response(call('history_describe', {})), response(call('history_aggregate', args, 'c2')),
                          response(message('基准样本共186单。')))
        result = self.agent(llm).ask('基准样本的订单数和收款？')
        self.assertIn('每组至少5个订单', llm.requests[0]['instructions'])
        self.assertTrue(result['snapshot_versions'][0].endswith('history-backtest-1.1'))
        self.assertEqual(result['data_source']['kind'], 'historical_backtest')
        self.assertEqual(result['grounding']['ungrounded_numbers'], [])

    def test_ungrounded_answer_gets_one_repair_round(self):
        args = {'scope': 'baseline', 'by': 'all', 'start_date': None, 'end_date': None}
        llm = ScriptedLLM(response(call('history_aggregate', args)), response(message('共999单。')),
                          response(message('基准样本共186单。')))
        result = self.agent(llm).ask('基准样本有多少单？')
        self.assertEqual(result['answer'], '基准样本共186单。')
        self.assertTrue(result['grounding']['repair_attempted'])
        self.assertIn('999', llm.requests[-1]['input'][-1]['content'])

    def test_unrepaired_answer_is_flagged_not_hidden(self):
        args = {'scope': 'baseline', 'by': 'all', 'start_date': None, 'end_date': None}
        llm = ScriptedLLM(response(call('history_aggregate', args)), response(message('共999单。')),
                          response(message('共998单。')))
        result = self.agent(llm).ask('基准样本有多少单？')
        self.assertEqual(result['grounding']['ungrounded_numbers'], ['998'])


class OracleAgent:
    """Calls the golden tool with golden arguments and states the expected key numbers."""
    def __init__(self, tools, cases, min_orders, fabricate=None):
        self.tools, self.cases, self.min_orders, self.fabricate = tools, {c['question']: c for c in cases}, min_orders, fabricate

    def ask(self, question):
        case = self.cases[question]
        tool = eval_runner.TOOLS[case['operation']]
        arguments = json.dumps(case['arguments'], ensure_ascii=False)
        result = self.tools.execute(tool, arguments, question)
        expected = eval_runner.expected_result(case, self.min_orders)
        parts = []
        for _, value in eval_runner.key_numbers(case, expected):
            parts.append(value[1][:10] if isinstance(value, tuple) else str(value))
        answer = '；'.join(parts) or '工具已返回。'
        if self.fabricate == case['case_id']:
            answer += '；另有98765元。'
        from socialdog_agent.grounding import ungrounded_numbers
        return {'answer': answer, 'evidence': [{'tool': tool, 'arguments': arguments, 'result': result}],
                'grounding': {'ungrounded_numbers': ungrounded_numbers(answer, [question, result]), 'repair_attempted': False},
                'request_id': 'oracle'}


class EvalRunnerTest(HistoryPackageMixin, unittest.TestCase):
    def test_every_golden_case_passes_for_a_correct_agent(self):
        records = eval_runner.run_cases(OracleAgent(self.tools(), self.cases, 5), self.cases, 5)
        failed = {r['case_id']: r['auto_notes'] for r in records if not r['auto_pass']}
        self.assertEqual(failed, {})
        self.assertIn('P01', {r['case_id'] for r in records})

    def test_fabricated_number_and_wrong_arguments_fail(self):
        case = next(c for c in self.cases if c['case_id'] == 'A01')
        record = eval_runner.run_cases(OracleAgent(self.tools(), self.cases, 5, fabricate='A01'), [case], 5)[0]
        self.assertFalse(record['auto_pass'])
        self.assertEqual(record['ungrounded_numbers'], ['98765'])
        wrong = dict(case, arguments={**case['arguments'], 'scope': 'all'})
        graded = eval_runner.grade(case, OracleAgent(self.tools(), [wrong], 5).ask(case['question']), None, 5)
        self.assertFalse(graded['tool_called'])
        self.assertFalse(graded['auto_pass'])

    def test_outputs_and_review_sheet(self):
        records = eval_runner.run_cases(OracleAgent(self.tools(), self.cases, 5), self.cases[:3], 5)
        summary = eval_runner.write_outputs(self.root / 'run', records, {'model': 'oracle'})
        self.assertEqual(summary['auto_passed'], 3)
        sheet = (self.root / 'run' / 'review_sheet.csv').read_text(encoding='utf-8-sig')
        self.assertIn('推理过程(1-5)', sheet)


if __name__ == '__main__':
    unittest.main()
