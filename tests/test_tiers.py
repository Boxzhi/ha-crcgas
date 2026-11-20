"""Regression fixtures contain only anonymous tier and bill values."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "tiers", Path(__file__).resolve().parents[1] / "custom_components/crcgas/tiers.py"
)
tiers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tiers)


def payload(rows):
    return {"consPrcInfo": {"consPrcDetailList": rows}}


def row(code, limit, used, remain, price="0"):
    return dict(ruleCode=code, levGq=limit, levGqSum=used, levGqRemain=remain, catPrc=price)


class TierTests(unittest.TestCase):
    def test_captured_response(self):
        result = tiers.parse_tiers(payload([
            row("1", "420", "43.000000", "377.000000"),
            row("2", "660", "0.000000", "240.000000"),
            row("3", "999999999999", "0.000000", "999999999339.000000"),
        ]))
        self.assertEqual([result[f"step{i}_gas_limit"] for i in (1, 2, 3)],
                         [420, 240, 999999999339])
        self.assertEqual([result[f"step{i}_gas_sum"] for i in (1, 2, 3)], [43, 0, 0])
        self.assertEqual([result[f"step{i}_remain"] for i in (1, 2, 3)],
                         [377, 240, 999999999339])
        self.assertIsNone(result["gas_price_step1"])

    def test_legacy_independent_and_out_of_order(self):
        result = tiers.parse_tiers(payload([
            row("0202", 170, 20, 150, "4"),
            row("0201", 330, 330, 0, "3.19"),
        ]))
        self.assertEqual(result["step2_gas_limit"], 170)
        self.assertEqual(result["step2_gas_sum"], 20)
        self.assertEqual(result["gas_price_step1"], 3.19)

    def test_legacy_cumulative_and_third_tier(self):
        result = tiers.parse_tiers(payload([
            row("0201", 330, 330, 0),
            row("0202", 500, 170, 0),
            row("0203", 999999999999, 12, 999999999487),
        ]))
        self.assertEqual(result["step2_gas_limit"], 170)
        self.assertEqual(result["step3_gas_sum"], 12)

    def test_missing_invalid_and_duplicate(self):
        for source in (None, {"consPrcInfo": None}, payload([]),
                       payload([row("2", 660, 0, 240)]),
                       payload([row("1", 420, None, 377)]),
                       payload([row("1", 420, 43, 0)]),
                       payload([row("1", 420, 43, 377)] * 2),
                       payload([row("1", "NaN", 0, 420)])):
            with self.subTest(source=source), self.assertRaises(ValueError):
                tiers.parse_tiers(source)

    def test_real_zero(self):
        result = tiers.parse_tiers(payload([row(1, 420, 420, 0)]))
        self.assertEqual(result["step1_remain"], 0)

    def test_bill_price_and_split(self):
        result = tiers.parse_bill_steps(
            [{"stepType": "一阶梯用气量", "gasUsed": 19, "stepAmt": 60.61}], 19
        )
        self.assertEqual(result, {"step1_gas_used": 19, "step2_gas_used": 0,
                                  "step3_gas_used": 0, "_bill_price_step1": 3.19})

    def test_cross_tier_and_missing_detail(self):
        result = tiers.parse_bill_steps([
            {"stepType": "一阶梯用气量", "gasUsed": 2, "stepAmt": 6.38},
            {"stepType": "二阶梯用气量", "gasUsed": 3, "stepAmt": 12},
            {"stepType": "三阶梯用气量", "gasUsed": 4, "stepAmt": 20},
        ], 9)
        self.assertEqual(result["step3_gas_used"], 4)
        self.assertEqual(result["_bill_price_step2"], 4)
        self.assertEqual(tiers.parse_bill_steps([], 9), {})
        self.assertEqual(tiers.parse_bill_steps(
            [{"stepType": "一阶梯", "gasUsed": 2}], 9), {})


if __name__ == "__main__":
    unittest.main()
