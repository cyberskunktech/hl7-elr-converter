"""
Checks that the pipeline does what we say it does.

Run from the project folder:  python -m unittest discover tests -v
"""
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
import generate_data  # noqa: E402
import pipeline  # noqa: E402


class TestEpiWeeks(unittest.TestCase):
    def test_known_mmwr_weeks(self):
        known = {
            date(2018, 12, 30): (2019, 1),  # epiweeks documentation example
            date(2019, 1, 5): (2019, 1),    # CDC example: week 1 of 2019 ends Jan 5
            date(2024, 12, 29): (2025, 1),  # late December can belong to next year
            date(2026, 1, 3): (2025, 53),   # early January can belong to last year
            date(2026, 1, 4): (2026, 1),
            date(2026, 3, 15): (2026, 11),
        }
        for d, expected in known.items():
            self.assertEqual(pipeline.mmwr_week(d), expected, d)


class TestCleaningRules(unittest.TestCase):
    def test_only_allowed_date_formats(self):
        parsed = pipeline.parse_dates(pd.Series(["2026-03-04", "03/04/2026", "04.03.2026", "2026-13-05"]))
        self.assertEqual(parsed[0], pd.Timestamp("2026-03-04"))
        self.assertEqual(parsed[1], pd.Timestamp("2026-03-04"))
        self.assertTrue(pd.isna(parsed[2]) and pd.isna(parsed[3]))

    def test_disease_spellings_map_to_one_name(self):
        raw = pd.DataFrame({c: ["x"] * 3 for c in
                            ["report_id", "patient_id", "case_status", "onset_date", "report_date",
                             "age", "sex", "district", "reporting_facility", "hospitalized", "died"]})
        raw["disease"] = ["Whooping Cough", " PERTUSSIS ", "pertussis"]
        self.assertEqual(set(pipeline.standardize(raw)["disease"]), {"Pertussis"})


class TestDeduplication(unittest.TestCase):
    def reports(self, dates):
        n = len(dates)
        return pd.DataFrame({
            "report_id": [f"R{i}" for i in range(n)], "patient_id": "P1", "disease": "Salmonellosis",
            "case_status": ["Suspect"] + ["Confirmed"] * (n - 1), "onset_date": pd.NaT,
            "report_date": pd.to_datetime(dates), "age": 30.0, "sex": "Female", "district": "Northgate",
            "hospitalized": [None] + ["Yes"] * (n - 1), "died": "No", "quality_flags": "",
        })

    def test_close_reports_merge_and_keep_strongest_values(self):
        cases, _ = pipeline.deduplicate(self.reports(["2026-02-01", "2026-02-04"]))
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases.loc[0, "case_status"], "Confirmed")
        self.assertEqual(cases.loc[0, "hospitalized"], "Yes")

    def test_reports_months_apart_stay_separate(self):
        cases, _ = pipeline.deduplicate(self.reports(["2026-01-10", "2026-05-20"]))
        self.assertEqual(len(cases), 2)


class TestEndToEnd(unittest.TestCase):
    """Generate fresh synthetic data, run the pipeline, compare to the answer key."""

    @classmethod
    def setUpClass(cls):
        generate_data.main()
        cls.cases, cls.links, cls.rejected = pipeline.run()
        cls.key = pd.read_csv(config.ANSWER_KEY_FILE)

    def test_rejects_exactly_the_unusable_reports(self):
        expected = set(self.key.loc[self.key["expected_outcome"] == "reject", "report_id"])
        self.assertEqual(set(self.rejected["report_id"]), expected)

    def test_each_case_matches_one_true_illness(self):
        merged = self.links.merge(self.key, on="report_id")
        per_case = merged.groupby("case_id")["true_case_id"].nunique()
        per_truth = merged.groupby("true_case_id")["case_id"].nunique()
        self.assertTrue((per_case == 1).all(), "a case mixed reports from different illnesses")
        self.assertTrue((per_truth == 1).all(), "one illness was split into several cases")

    def test_case_count_matches_truth(self):
        kept = self.key[self.key["expected_outcome"] == "keep"]
        self.assertEqual(len(self.cases), kept["true_case_id"].nunique())

    def test_planted_outbreak_is_visible(self):
        ob = config.OUTBREAK
        in_window = self.cases["event_date"].between(ob["start"], ob["end"])
        measles = self.cases[(self.cases["disease"] == ob["disease"]) & in_window]
        self.assertGreater((measles["district"] == ob["district"]).mean(), 0.8)


if __name__ == "__main__":
    unittest.main()
