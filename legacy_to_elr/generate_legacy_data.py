"""
Step 0: make synthetic legacy lab exports from three different sources.

Each source looks the way older systems really do: its own column names, its
own internal test codes, its own date formats and sex codes, and its own gaps.

  CHL  Central Hospital Laboratory   CSV, compact codes, full demographics
  RVC  Riverside clinic              CSV from an old EHR, free-text test names,
                                     no specimen column
  RRL  Regional Reference Laboratory pipe-delimited legacy extract, age instead
                                     of birth date, ZIP code only for address

An answer key records what SHOULD happen to every row. Real data never comes
with one; ours does so the tests can check the converter's work.

Run:  python generate_legacy_data.py
"""
import numpy as np
import pandas as pd

import config

rng = np.random.default_rng(config.RANDOM_SEED)

FIRST = ["Amara", "Ben", "Chloe", "Daniel", "Esther", "Farid", "Grace", "Hugo", "Ines",
         "Jamal", "Keiko", "Liam", "Maya", "Noah", "Olivia", "Priya", "Quinn", "Rosa",
         "Samuel", "Tendai", "Uma", "Victor", "Wen", "Yara", "Zane"]
LAST = ["Abara", "Brooks", "Castillo", "Dube", "Ellison", "Fontaine", "Gupta", "Hartley",
        "Ibsen", "Jovanovic", "Kimura", "Lindqvist", "Moyo", "Novak", "O'Neil", "Park",
        "Quintero", "Reyes", "Sato", "Thornton", "Udeh", "Varga", "Whitfield", "Yilmaz"]
STREETS = ["Maple Street", "Oak Avenue", "Cedar Lane", "Pine Road", "Elm Court", "Birch Way"]
CITIES = {"00001": "Northgate", "00002": "Riverside", "00003": "Eastfield",
          "00004": "Westbrook", "00005": "Southvale"}
PROVIDERS = [("1990000011", "Adeyemi", "Kola"), ("1990000022", "Bergstrom", "Lena"),
             ("1990000033", "Chowdhury", "Ravi"), ("1990000044", "Delgado", "Marta")]

# Each source's local test codes (the last one in each list is NOT in our
# mapping table, so the converter must put it on the "needs mapping" list).
TESTS = {
    "CHL": ["HAVM", "MEASM", "BPPCR", "SALPCR", "CAMPCR"],
    "RVC": ["Hep A IgM", "Measles IgM", "Pertussis PCR"],
    "RRL": ["HAV.IGM", "B.PERT.PCR", "SALM.PCR", "CAMPY.PCR"],
}
UNMAPPED_TEST = {"CHL": "LYMEAB", "RVC": "Mumps IgM", "RRL": "NORO.PCR"}
SPECIMEN_FOR = {  # which specimen goes with which test, in each source's own words
    "CHL": {"HAVM": "SER", "MEASM": "SER", "BPPCR": "NPS", "SALPCR": "STL", "CAMPCR": "STL",
            "LYMEAB": "SER"},
    "RRL": {"HAV.IGM": "SERUM", "B.PERT.PCR": "NP SWAB", "SALM.PCR": "STOOL",
            "CAMPY.PCR": "STOOL", "NORO.PCR": "STOOL"},
}
RESULTS = {  # (positive, negative, inconclusive) in each source's own words
    "CHL": ("POS", "NEG", "IND"),
    "RVC": (["Detected", "Positive"], ["Not Detected", "Negative"], None),
    "RRL": ("D", "ND", "INC"),
}
ORDERING = {
    "CHL": ["Central Hospital Emergency Department", "Central Hospital Pediatrics Clinic",
            "Central Hospital Infectious Disease Clinic"],
    "RRL": ["Eastfield Urgent Care", "Westbrook Pediatrics", "Southvale Family Practice"],
}

# Problems we plant on purpose: (problem, how many rows, expected outcome)
PLANTED = {
    "CHL": [("unmapped_test", 3, "needs_mapping"), ("missing_patient_id", 2, "rejected"),
            ("bad_collection_date", 2, "rejected")],
    "RVC": [("unmapped_test", 3, "needs_mapping"), ("unmapped_result", 2, "needs_mapping"),
            ("bad_collection_date", 2, "rejected")],
    "RRL": [("unmapped_test", 3, "needs_mapping"), ("facility_not_in_directory", 3, "rejected"),
            ("missing_patient_id", 2, "rejected")],
}
N_DUPLICATES = 3


def base_rows(source):
    """Invent the true lab results for one source."""
    n = config.ROWS_PER_SOURCE
    start = pd.Timestamp(config.COLLECTION_START)
    span = (pd.Timestamp(config.COLLECTION_END) - start).days
    collected = (start + pd.to_timedelta(rng.integers(0, span, n), unit="D")
                 + pd.to_timedelta(rng.integers(7 * 60, 19 * 60, n), unit="min"))
    resulted = collected + pd.to_timedelta(rng.integers(20, 96, n), unit="h")
    dob = pd.Timestamp("1940-01-01") + pd.to_timedelta(rng.integers(0, 365 * 84, n), unit="D")
    zips = rng.choice(list(CITIES), n)
    provider = [PROVIDERS[i] for i in rng.integers(0, len(PROVIDERS), n)]
    outcome_draw = rng.random(n)
    return pd.DataFrame({
        "test": rng.choice(TESTS[source], n),
        "result_kind": np.where(outcome_draw < 0.55, "pos",
                                np.where(outcome_draw < 0.95, "neg", "inc")),
        "first": rng.choice(FIRST, n), "last": rng.choice(LAST, n),
        "dob": dob, "sex": rng.choice(["M", "F", "U"], n, p=[0.48, 0.49, 0.03]),
        "street": [f"{rng.integers(1, 999)} {s}" for s in rng.choice(STREETS, n)],
        "zip": zips, "city": [CITIES[z] for z in zips],
        "phone": [f"555-555-{rng.integers(1000, 9999)}" for _ in range(n)],
        "collected": collected, "resulted": resulted,
        "npi": [p[0] for p in provider], "prov_last": [p[1] for p in provider],
        "prov_first": [p[2] for p in provider],
        "facility": rng.choice(ORDERING.get(source, ["(clinic)"]), n),
        "record_id": [f"{source}{26000 + i}" for i in range(n)],
        "patient_id": [f"{source[0]}{rng.integers(100000, 999999)}" for _ in range(n)],
    })


def plant_problems(source, rows):
    """Apply planted problems to positive rows only, so each row's fate is unambiguous."""
    rows = rows.copy()
    rows["problem"] = "none"
    rows["expected"] = np.where(rows["result_kind"] == "pos", "sent", "not_reportable")
    if source == "RVC":  # this clinic records no inconclusive results
        rows.loc[rows["result_kind"] == "inc", ["result_kind", "expected"]] = ["neg", "not_reportable"]
    positives = list(rows.index[rows["result_kind"] == "pos"])
    rng.shuffle(positives)
    for problem, count, expected in PLANTED[source]:
        chosen, positives = positives[:count], positives[count:]
        rows.loc[chosen, ["problem", "expected"]] = [problem, expected]
    # Duplicates: the same clean, positive result exported a second time
    originals = rows.loc[positives[:N_DUPLICATES]].copy()
    originals["problem"], originals["expected"] = "duplicate", "duplicate"
    return pd.concat([rows, originals], ignore_index=True)


def result_text(source, kind, problem):
    if problem == "unmapped_result":
        return "Pos."
    pos, neg, inc = RESULTS[source]
    value = {"pos": pos, "neg": neg, "inc": inc}[kind]
    return rng.choice(value) if isinstance(value, list) else value


def render(source, rows):
    """Write rows in the source's own legacy layout."""
    out = []
    for _, r in rows.iterrows():
        test = UNMAPPED_TEST[source] if r["problem"] == "unmapped_test" else r["test"]
        pid = "" if r["problem"] == "missing_patient_id" else r["patient_id"]
        res = result_text(source, r["result_kind"], r["problem"])
        if source == "CHL":
            coll = "20261345" if r["problem"] == "bad_collection_date" else f"{r['collected']:%Y%m%d%H%M}"
            out.append({"ACCESSION": r["record_id"], "MRN": pid, "PT_LAST": r["last"].upper(),
                        "PT_FIRST": r["first"].upper(), "DOB": f"{r['dob']:%Y%m%d}", "SEX": r["sex"],
                        "ADDR1": r["street"], "CITY": r["city"], "ST": config.STATE, "ZIP": r["zip"],
                        "PHONE": r["phone"], "TEST_CD": test, "RESULT": res,
                        "SPEC": SPECIMEN_FOR["CHL"][test], "COLL_DTTM": coll,
                        "RSLT_DTTM": f"{r['resulted']:%Y%m%d%H%M}", "ORD_NPI": r["npi"],
                        "ORD_PROV": f"{r['prov_last']}, {r['prov_first']}", "ORD_LOC": r["facility"]})
        elif source == "RVC":
            coll = "13/45/2026 10:00" if r["problem"] == "bad_collection_date" else f"{r['collected']:%m/%d/%Y %H:%M}"
            sex = {"M": "1", "F": "2", "U": "9"}[r["sex"]]
            messy_test = test if rng.random() < 0.6 else f" {test.lower()} "
            out.append({"Order #": r["record_id"], "Patient ID": pid,
                        "Patient Name": f"{r['last']}, {r['first']}", "Birth Date": f"{r['dob']:%m/%d/%Y}",
                        "Gender": sex, "Street": r["street"], "City": r["city"], "Zip": r["zip"],
                        "Test": messy_test, "Result": res, "Collected": coll,
                        "Reported": f"{r['resulted']:%m/%d/%Y %H:%M}",
                        "Provider": f"{r['prov_first']} {r['prov_last']}"})
        else:
            sex = {"M": "MALE", "F": "FEMALE", "U": "UNK"}[r["sex"]]
            age = int((r["collected"] - r["dob"]).days // 365.25)
            fac = "Northgate Walk-In Clinic" if r["problem"] == "facility_not_in_directory" else r["facility"]
            out.append({"ACC": r["record_id"], "PID": pid, "NAME": f"{r['last'].upper()}^{r['first'].upper()}",
                        "AGE": age, "SX": sex, "ZIP": r["zip"], "TST": test, "RES": res,
                        "SRC": SPECIMEN_FOR["RRL"][test], "DCOLL": f"{r['collected']:%Y-%m-%d}",
                        "DRES": f"{r['resulted']:%Y-%m-%d}", "ORDFAC": fac,
                        "ORDDOC": f"{r['prov_last'].upper()}^{r['prov_first'].upper()}"})
    return pd.DataFrame(out)


def main():
    sources = pd.read_csv(config.MAPPINGS_DIR / "sources.csv", dtype=str, keep_default_na=False)
    config.LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    keys = []
    for _, src in sources.iterrows():
        sid = src["source_id"]
        rows = plant_problems(sid, base_rows(sid))
        rows = rows.sample(frac=1, random_state=config.RANDOM_SEED).reset_index(drop=True)
        rows["row_number"] = range(1, len(rows) + 1)
        # Whichever copy appears first in the file is sent; any later copy is the duplicate
        dup_ids = set(rows.loc[rows["problem"] == "duplicate", "record_id"])
        pair = rows["record_id"].isin(dup_ids)
        later = pair & rows.duplicated("record_id", keep="first")
        rows.loc[pair, ["problem", "expected"]] = ["none", "sent"]
        rows.loc[later, ["problem", "expected"]] = ["duplicate", "duplicate"]
        render(sid, rows).to_csv(config.LEGACY_DIR / src["file"], index=False, sep=src["delimiter"])
        keys.append(pd.DataFrame({"source_id": sid, "row_number": rows["row_number"],
                                  "record_id": rows["record_id"], "planted_problem": rows["problem"],
                                  "expected_outcome": rows["expected"]}))
        print(f"{sid}: wrote {len(rows)} rows to {src['file']}")
    key = pd.concat(keys, ignore_index=True)
    # Build the path at run time, so it follows LEGACY_DIR if that setting changes
    key.to_csv(config.LEGACY_DIR / config.ANSWER_KEY_FILE.name, index=False)
    print("\nExpected outcomes:")
    print(key.groupby(["source_id", "expected_outcome"]).size().unstack(fill_value=0).to_string())


if __name__ == "__main__":
    main()
