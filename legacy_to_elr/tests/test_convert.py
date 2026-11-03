"""
Checks that the converter does what we say it does.

Run from the project folder:  python -m unittest discover tests -v
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
import convert  # noqa: E402
import generate_legacy_data  # noqa: E402
from hl7_builder import esc, fld, get, parse, unescape  # noqa: E402


class TestHL7Basics(unittest.TestCase):
    def test_special_characters_are_escaped_and_restored(self):
        text = "Health & Wellness | Clinic ^ 1 ~ A\\B"
        escaped = esc(text)
        for char in "|^&~":
            self.assertNotIn(char, escaped)
        self.assertEqual(unescape(escaped), text)

    def test_profile_matches_the_national_value(self):
        # ONC ELR validation tool rule ELR-22, from the HL7 ELR R1 errata
        self.assertEqual(fld(*config.MESSAGE_PROFILE),
                         "PHLabReport-NoAck^^2.16.840.1.113883.9.11^ISO")

    def test_reading_subcomponents(self):
        p = parse("MSH|^~\\&#|A\rSPM|1|^CHL1&CHL&2.16.840.1.113883.19.4.11&ISO\r")
        self.assertEqual(get(p, "SPM-2.2.3"), "2.16.840.1.113883.19.4.11")
        self.assertEqual(get(p, "SPM-2.2.4"), "ISO")


class TestStrictDates(unittest.TestCase):
    def test_lenient_date_is_rejected(self):
        raw = pd.DataFrame({"row_number": [1, 2], "X": ["202609150942", "20261345"]})
        fm = pd.DataFrame({"source_id": ["T", "T"], "common_field": ["collected_at", "record_id"],
                           "source_column": ["X", "X"], "format": ["datetime:%Y%m%d%H%M", ""],
                           "constant": ["", ""]})
        out = convert.harmonize(raw, "T", fm)
        self.assertEqual(out.loc[0, "collected_at"], pd.Timestamp("2026-09-15 09:42"))
        self.assertTrue(pd.isna(out.loc[1, "collected_at"]))


class TestEndToEnd(unittest.TestCase):
    """Generate fresh synthetic files, convert them, compare to the answer key."""

    @classmethod
    def setUpClass(cls):
        generate_legacy_data.main()
        cls.df, cls.messages = convert.run()
        cls.key = pd.read_csv(config.ANSWER_KEY_FILE, dtype=str)
        cls.tables = convert.load_tables()

    def test_every_row_has_the_expected_outcome(self):
        got = self.df[["source_id", "row_number", "status"]].astype(str)
        merged = self.key.merge(got, on=["source_id", "row_number"])
        self.assertEqual(len(merged), len(self.key))
        wrong = merged[merged["expected_outcome"] != merged["status"]]
        self.assertTrue(wrong.empty, f"\n{wrong.to_string()}")

    def test_every_message_passes_validation(self):
        self.assertGreater(len(self.messages), 0)
        for msg in self.messages:
            self.assertEqual(convert.validate_message(msg, self.tables), [])

    def test_messages_carry_the_source_values(self):
        """Read each message back and confirm key fields match the converted row."""
        sent = self.df[self.df["status"] == "sent"].set_index("control_id")
        for msg in self.messages:
            p = parse(msg)
            row = sent.loc[get(p, "MSH-10")]
            self.assertEqual(get(p, "PID-3.1"), row["patient_id"])
            self.assertEqual(get(p, "OBX-3.1"), row["loinc_code"])
            self.assertIn(get(p, "OBX-5.1"), config.REPORTABLE_RESULT_CODES)
            self.assertEqual(get(p, "ORC-21"), row["ordering_facility"])

    def test_messages_declare_all_five_encoding_characters(self):
        for msg in self.messages:
            self.assertTrue(msg.startswith("MSH|^~\\&#|"))

    def test_validator_catches_every_problem_the_onc_tool_found(self):
        """Put each error from the first ONC validation back in, and check our validator sees it."""
        good = next(m for m in self.messages if "NPI&" in m)
        self.assertEqual(convert.validate_message(good, self.tables), [])
        breakages = {
            "ELR-22": good.replace("2.16.840.1.113883.9.11", "2.16.840.1.114222.4.10.3"),
            "ELR-013": good.replace("MSH|^~\\&#|", "MSH|^~\\&|", 1),
            "ELR-010": good.replace("^MA^", "^XX^"),
            "ELR-004": good.replace("^2.16.840.1.113883.19.4.11^ISO", "^99D0000001^CLIA", 1),
            "assigning authority": good.replace("NPI&2.16.840.1.113883.4.6&ISO", ""),
            "SPM-18": "\r".join(seg.rsplit("|", 1)[0] if seg.startswith("SPM") else seg
                                 for seg in good.split("\r")),
            "truncation": good.replace("Bergstrom", "Bergstrom#"),
        }
        for label, broken in breakages.items():
            with self.subTest(label):
                self.assertNotEqual(convert.validate_message(broken, self.tables), [], label)

    def test_message_ids_are_unique(self):
        ids = [get(parse(m), "MSH-10") for m in self.messages]
        self.assertEqual(len(ids), len(set(ids)))

    def test_gap_report_finds_the_known_gaps(self):
        gaps = convert.gap_report(self.df, self.tables).set_index(["source_id", "field"])
        self.assertEqual(gaps.loc[("RRL", "Date of birth"), "status"], "NOT COLLECTED")
        self.assertEqual(gaps.loc[("RVC", "Specimen type (SNOMED CT)"), "how_obtained"],
                         "derived from the LOINC test code")
        for sid in ["CHL", "RVC", "RRL"]:
            self.assertEqual(gaps.loc[(sid, "Race"), "status"], "NOT COLLECTED")
            self.assertEqual(gaps.loc[(sid, "Specimen received date/time"), "status"], "OK")


if __name__ == "__main__":
    unittest.main()
