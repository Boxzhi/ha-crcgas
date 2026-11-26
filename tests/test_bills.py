"""Regression tests for anonymized empty-application-number bills."""
import importlib.util
import sys
import types
from pathlib import Path
import unittest

pkg = types.ModuleType("crcgas_bill_test")
pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/crcgas")]
sys.modules[pkg.__name__] = pkg
from crcgas_bill_test.bills import (merge_bill, previous_usage, read_date, monthly_totals, select_detail, detail_complete)


class BillTests(unittest.TestCase):
    def test_blank_application_and_repeat(self):
        rows = []
        bill = dict(id="new", applicationNo="", billYm="2026-11", gasAmt=28, billAmt=89.32)
        self.assertEqual(merge_bill(rows, bill, "now"), "new")
        self.assertEqual(merge_bill(rows, bill, "later"), "updated")
        self.assertEqual(len(rows), 1)
        self.assertEqual(monthly_totals(rows), {"2026-11": (28, 89.32)})

    def test_legacy_alias(self):
        rows = [dict(applicationNo="old", billYm="2026-10", gasAmt=19, billAmt=60.61)]
        self.assertEqual(merge_bill(rows, dict(id="id1", **rows[0]), "now"), "updated")
        self.assertEqual(rows[0]["id"], "id1")
        self.assertEqual(merge_bill(rows, dict(id="id2", applicationNo="", billYm="2026-10", gasAmt=0, billAmt=0), "now"), "new")
        self.assertEqual(len(rows), 2)

    def test_invalid_and_zero(self):
        rows = []
        self.assertEqual(merge_bill(rows, dict(billYm="2026-11", gasAmt=1, billAmt=1), "now"), "invalid")
        self.assertEqual(merge_bill(rows, dict(id="zero", billYm="2026-11", gasAmt=0, billAmt=0), "now"), "new")

    def test_previous_period(self):
        chart = {"thisGas": [6,4,33,33,32,18,9,1,12,19,28,None],
                 "lastGas": [None,11,35,22,29,14,5,None,11,12,28,15]}
        self.assertEqual(previous_usage([], "2026-11", chart, 2026), 19)
        self.assertEqual(previous_usage([], "2026-01", chart, 2026), 15)
        self.assertEqual(previous_usage([dict(billYm="2026-10", gasAmt=0)], "2026-11", chart, 2026), 0)
        self.assertIsNone(previous_usage([], "2023-01", chart, 2026))

    def test_dates(self):
        self.assertEqual(read_date("2026/11/24"), "2026-11-24")
        self.assertEqual(read_date("2026-10-24"), "2026-10-24")
        self.assertEqual(read_date("2026-02-30"), "未知")

    def test_preserve_id_when_legacy_row_arrives(self):
        rows = [dict(id="one", applicationNo="old", billYm="2026-10", gasAmt=19, billAmt=60.61)]
        row = dict(rows[0], id="")
        self.assertEqual(merge_bill(rows, row, "now"), "updated")
        self.assertEqual(rows[0]["id"], "one")

    def test_detail_fixture_and_wrong_id(self):
        row = dict(id="one", thisRead=377, lastRead=349, gasUsed=28,
                   thisReadTime="2026/11/24", lastReadTime="2026-10-24")
        self.assertEqual(select_detail([row], "one"), row)
        self.assertTrue(detail_complete(row))
        self.assertIsNone(select_detail([row], "different"))
        self.assertFalse(detail_complete(dict(row, thisReadTime="")))
