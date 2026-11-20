"""Anonymous fixtures for payment history and pagination."""
import asyncio
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "payments", Path(__file__).resolve().parents[1] / "custom_components/crcgas/payments.py"
)
payments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payments)


def row(time, amount="100.00", charge="01", channel="0406", state="缴费成功"):
    return {"payTime": time, "payAmount": amount, "chargeType": charge,
            "feeType": charge, "payType": channel, "payState": state}


def response(rows):
    return {"success": True, "dataResult": rows}


class FakeAPI:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def async_query_pay_history(self, cons_no, **kw):
        self.calls.append(kw)
        return self.pages[kw["page"] - 1]


class PaymentTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_repeated_page(self):
        rows = [row(f"2026-01-{i:02d} 10:00:00") for i in range(1, 8)]
        api = FakeAPI([response(rows), response(list(reversed(rows)))])
        result = await payments.fetch_payments(api, "test", 2026)
        self.assertEqual(len(result), 7)
        self.assertEqual(result[0]["payTime"], "2026-01-07 10:00:00")
        self.assertEqual(api.calls[0], dict(page=1, page_num=6, start_ym="2000-01", end_ym="2100-12"))

    async def test_paged_and_same_second(self):
        first = [row(f"2026-01-{i:02d} 10:00:00") for i in range(1, 7)]
        second = [row("2024-04-16 17:07:20", "100.00", "02", "0101"),
                  row("2024-04-16 17:07:20", "200.00", "02", "0101"),
                  row("2024-04-16 17:07:20", "200.00", "02", "0101")]
        result = await payments.fetch_payments(FakeAPI([response(first), response(second)]), "test", 2026)
        self.assertEqual(len(result), 9)
        self.assertEqual(payments.summarize_year(result, 2024)["business_amount"], 500)

    async def test_exact_page_and_empty_end(self):
        rows = [row(f"2026-01-{i:02d} 10:00:00") for i in range(1, 7)]
        result = await payments.fetch_payments(FakeAPI([response(rows), response([])]), "test", 2026)
        self.assertEqual(len(result), 6)

    async def test_true_empty(self):
        result = await payments.fetch_payments(FakeAPI([response([])]), "test", 2026)
        self.assertEqual(result, [])
        self.assertEqual(payments.summarize([], 2026)["annual_pay_count"], 0)
        self.assertIsNone(payments.summarize([], 2026)["last_pay_amount"])

    async def test_incomplete_or_overlapping_page_rejected(self):
        rows = [row(f"2026-01-{i:02d} 10:00:00") for i in range(1, 7)]
        for bad in ({"success": False}, response(None), response(rows),
                    response([rows[0], row("2026-02-01 10:00:00")])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                await payments.fetch_payments(FakeAPI([response(rows), bad]), "test", 2026)

    async def test_window_and_success_only(self):
        rows = [row("2023-12-31 10:00:00"), row("2026-01-01 10:00:00", "0.00"),
                row("2026-02-01 10:00:00", "99.00", state="缴费失败"),
                row("2026-03-01 10:00:00", "30.00", "02", "0101")]
        records = await payments.fetch_payments(FakeAPI([response(rows)]), "test", 2026)
        summary = payments.summarize(records, 2026)
        self.assertEqual(len(records), 3)
        self.assertEqual(summary["annual_pay_count"], 1)
        self.assertEqual(summary["last_pay_amount"], 0)
        self.assertEqual(summary["annual_business_pay_amount"], 30)

    def test_unknown_code_and_invalid_amount(self):
        result = payments.normalize_payment(row("2026-01-01 10:00:00", "100", "99", "9999"))
        self.assertEqual(result["category"], "unknown")
        self.assertIn("9999", result["payTypeName"])
        for invalid in ("NaN", "", "Infinity"):
            with self.assertRaises(ValueError):
                payments.normalize_payment(row("2026-01-01 10:00:00", invalid))


if __name__ == "__main__":
    unittest.main()
