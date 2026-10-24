import json
import tempfile
import unittest
from pathlib import Path

import benchmark
from router import (RouterError, analyze, clean_prompt, cost_of, est_tokens, execute_plan,
                    load_config, log_decision, make_plan, pick_tier, split_request)


class CleanTests(unittest.TestCase):
    def test_removes_politeness(self):
        self.assertEqual(
            clean_prompt("Hi! Could you please tell me what is the capital of France? Thanks in advance!"),
            "Tell me what is the capital of France?")

    def test_quoted_text_is_untouched(self):
        out = clean_prompt("Please extract the email from this text: 'please contact Priya, thanks'")
        self.assertIn("'please contact Priya, thanks'", out)
        self.assertTrue(out.startswith("Extract"))

    def test_cleaning_saves_tokens(self):
        raw = "Hello, I was wondering if you could please basically explain photosynthesis. Thank you!!!"
        self.assertLess(est_tokens(clean_prompt(raw)), est_tokens(raw))

    def test_apostrophes_do_not_confuse_quotes(self):
        self.assertEqual(clean_prompt("Please don't forget to list the colors"), "Don't forget to list the colors")


class SplitTests(unittest.TestCase):
    def test_numbered_list(self):
        self.assertEqual(split_request("1. What is 2+2?\n2. Name a color."), ["What is 2+2?", "Name a color."])

    def test_shared_context_is_kept(self):
        parts = split_request("About Paris:\n- Where is it?\n- Why is it famous?")
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(p.startswith("About Paris:") for p in parts))

    def test_two_questions(self):
        self.assertEqual(len(split_request("What is the capital of Peru? What is the capital of Chile?")), 2)

    def test_numbered_items_on_one_line(self):
        self.assertEqual(
            split_request("1. What is the capital of Japan? 2. Explain why the sky is blue, step by step."),
            ["What is the capital of Japan?", "Explain why the sky is blue, step by step."])

    def test_inline_numbers_keep_context_and_ignore_decimals(self):
        parts = split_request("Answer these: 1. Define entropy. 2. Name a color.")
        self.assertEqual(parts, ["Answer these:\nDefine entropy.", "Answer these:\nName a color."])
        self.assertEqual(split_request("Add 2.5 liters to 1.5 liters."), ["Add 2.5 liters to 1.5 liters."])

    def test_single_request_is_not_split(self):
        self.assertEqual(split_request("Explain gravity."), ["Explain gravity."])


class AnalyzeTests(unittest.TestCase):
    def tier(self, text):
        return pick_tier(analyze(clean_prompt(text))[0])

    def test_simple_goes_local(self):
        self.assertEqual(self.tier("What is the capital of France?"), "local")
        self.assertEqual(self.tier("Extract the email from: 'a@b.com'"), "local")
        self.assertEqual(self.tier("Convert 'hello' to uppercase."), "local")

    def test_arithmetic_goes_mid(self):
        self.assertEqual(self.tier("What is 17*23?"), "mid")

    def test_hard_goes_strong(self):
        self.assertEqual(self.tier("Explain why the halting problem is undecidable, step by step."), "strong")
        self.assertEqual(self.tier("Design a database schema and explain the trade-offs."), "strong")

    def test_reasons_are_given(self):
        score, reasons = analyze("Explain why this works")
        self.assertTrue(reasons and "reasoning" in reasons[0])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_multi_part_is_routed_separately(self):
        plan = make_plan("1. What is the capital of Japan?\n2. Explain why the sky is blue, step by step.", self.cfg)
        self.assertEqual([p["tier"] for p in plan["parts"]], ["local", "strong"])

    def test_one_line_numbered_request_is_routed_separately(self):
        plan = make_plan("1. What is the capital of Japan? 2. Explain why the sky is blue, step by step.", self.cfg)
        self.assertEqual([p["tier"] for p in plan["parts"]], ["local", "strong"])

    def test_override(self):
        plan = make_plan("What is the capital of France?", self.cfg, force="strong")
        p = plan["parts"][0]
        self.assertEqual((p["tier"], p["suggested"], p["overridden"]), ("strong", "local", True))

    def test_cost_math(self):
        t = {"price_in_per_m": 2.0, "price_out_per_m": 6.0}
        self.assertAlmostEqual(cost_of(t, 1_000_000, 500_000), 5.0)

    def test_execute_and_stitch_in_mock_mode(self):
        plan = make_plan("1. What is the capital of Japan?\n2. Define entropy.", self.cfg)
        result = execute_plan(plan, self.cfg)
        self.assertTrue(result["mock"])
        self.assertTrue(result["answer"].startswith("1. ") and "\n\n2. " in result["answer"])

    def test_log_is_written(self):
        plan = make_plan("What is the capital of France?", self.cfg)
        result = execute_plan(plan, self.cfg)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.jsonl"
            log_decision(path, plan, result, 0.1)
            entry = json.loads(path.read_text().splitlines()[0])
        self.assertEqual(entry["parts"][0]["tier"], "local")
        self.assertIn("cleaned", entry["parts"][0])

    def test_missing_tier_in_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text('{"tiers": {"local": {}}}')
            with self.assertRaises(RouterError):
                load_config(p)


class BenchmarkTests(unittest.TestCase):
    def test_router_is_cheaper_in_mock_mode_and_quality_is_not_claimed(self):
        cfg = load_config()
        items = json.loads(Path(benchmark.__file__).with_name("bench_set.json").read_text())
        rows = benchmark.run(cfg, items)
        s = benchmark.summarize(rows)
        self.assertEqual(len(rows), len(items))
        self.assertLess(s["router_cost"], s["baseline_cost"])
        self.assertTrue(s["mock"])
        self.assertEqual(s["scored"], 0)

    def test_check(self):
        self.assertTrue(benchmark.check("It is PARIS.", ["paris"]))
        self.assertFalse(benchmark.check("London", ["paris"]))
        self.assertIsNone(benchmark.check("anything", []))


if __name__ == "__main__":
    unittest.main()