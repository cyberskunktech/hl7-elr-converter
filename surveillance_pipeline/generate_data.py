"""
Step 0: make synthetic (fake) disease case reports.

Nothing here is real patient data. The script invents people, gives each one a
disease, and writes the reports a health department would receive about them,
including the kinds of mistakes real reports contain.

It also writes an answer key (data/raw/answer_key.csv) recording the truth
behind every report. Real data never comes with one. Ours does, so we can check
whether the pipeline gets the right answer.

Run:  python generate_data.py
"""
import numpy as np
import pandas as pd

import config

rng = np.random.default_rng(config.RANDOM_SEED)

# (youngest, most common, oldest) age for each disease
AGE_SHAPE = {
    "Salmonellosis": (0, 30, 90),
    "Campylobacteriosis": (0, 35, 90),
    "Pertussis": (0, 2, 70),
    "Hepatitis A": (5, 38, 85),
    "Measles": (0, 5, 45),
}
HOSPITALIZATION_RISK = {
    "Salmonellosis": 0.20,
    "Campylobacteriosis": 0.12,
    "Pertussis": 0.10,
    "Hepatitis A": 0.35,
    "Measles": 0.20,
}
DEATH_RISK = 0.004
SEASONAL = {"Salmonellosis", "Campylobacteriosis"}  # these rise toward summer

# The many ways people type the same answer
SEX_STYLES = {"Female": ["F", "Female", "female", "F "], "Male": ["M", "Male", "male"],
              "Unknown": ["U", "Unknown", ""]}
YN_STYLES = {"Yes": ["Y", "Yes", "yes", "1"], "No": ["N", "No", "no", "0"],
             "Unknown": ["U", "Unk", ""]}

# Problems we plant on purpose, and the share of reports that get each one.
# "keep" problems can be fixed or worked around; "reject" problems make the
# report unusable until someone follows up with the reporter.
KEEP_PROBLEMS = {
    "disease_spelling_variant": 0.10,
    "us_date_format": 0.08,
    "missing_onset": 0.12,
    "onset_after_report": 0.02,
    "age_out_of_range": 0.015,
    "unknown_district": 0.015,
}
REJECT_PROBLEMS = {
    "missing_disease": 0.01,
    "unrecognized_disease": 0.01,
    "bad_report_date": 0.01,
    "missing_patient_id": 0.005,
}


def pick(options, weights, size):
    weights = np.asarray(weights, dtype=float)
    return rng.choice(options, size=size, p=weights / weights.sum())


def make_true_cases():
    """Invent the people who actually got sick (the truth)."""
    start = pd.Timestamp(config.PERIOD_START)
    n_days = (pd.Timestamp(config.PERIOD_END) - start).days + 1
    days = np.arange(n_days)

    # Everyday background cases
    n = config.N_BACKGROUND_CASES
    diseases = pick(list(config.DISEASES), list(config.DISEASES.values()), n)
    offsets = np.empty(n, dtype=int)
    for disease in np.unique(diseases):
        mask = diseases == disease
        weights = 1 + 1.5 * days / n_days if disease in SEASONAL else np.ones(n_days)
        offsets[mask] = rng.choice(days, size=mask.sum(), p=weights / weights.sum())
    background = pd.DataFrame({
        "disease": diseases,
        "onset_date": start + pd.to_timedelta(offsets, unit="D"),
        "district": pick(list(config.DISTRICTS), list(config.DISTRICTS.values()), n),
    })

    # The planted outbreak: rises quickly, then tails off
    ob = config.OUTBREAK
    ob_start = pd.Timestamp(ob["start"])
    ob_days = (pd.Timestamp(ob["end"]) - ob_start).days + 1
    ob_offsets = np.round(rng.triangular(0, ob_days * 0.35, ob_days - 1, ob["n_cases"])).astype(int)
    outbreak = pd.DataFrame({
        "disease": ob["disease"],
        "onset_date": ob_start + pd.to_timedelta(ob_offsets, unit="D"),
        "district": ob["district"],
    })

    cases = pd.concat([background, outbreak], ignore_index=True)
    n = len(cases)
    cases["patient_id"] = [f"P{x}" for x in rng.choice(np.arange(100000, 1000000), n, replace=False)]

    cases["age"] = 0
    for disease, (lo, mode, hi) in AGE_SHAPE.items():
        mask = cases["disease"] == disease
        cases.loc[mask, "age"] = np.round(rng.triangular(lo, mode, hi, mask.sum())).astype(int)

    cases["sex"] = pick(["Female", "Male", "Unknown"], [0.49, 0.49, 0.02], n)
    cases["case_status"] = pick(["Confirmed", "Probable", "Suspect"], [0.70, 0.20, 0.10], n)
    risk = cases["disease"].map(HOSPITALIZATION_RISK).to_numpy()
    cases["hospitalized"] = np.where(rng.random(n) < risk, "Yes", "No")
    cases["died"] = np.where(rng.random(n) < DEATH_RISK, "Yes", "No")
    cases["first_report_date"] = cases["onset_date"] + pd.to_timedelta(rng.integers(1, 15, n), unit="D")

    # Reinfections: same person, same disease, about four months later.
    # These are two real cases, and the pipeline must NOT merge them.
    period_end = pd.Timestamp(config.PERIOD_END)
    candidates = cases[cases["disease"].isin(SEASONAL)
                       & (cases["onset_date"] < start + pd.Timedelta(days=30))]
    chosen = candidates.sample(config.N_REINFECTIONS, random_state=config.RANDOM_SEED)
    second = chosen.copy()
    second["onset_date"] = chosen["onset_date"] + pd.to_timedelta(
        rng.integers(120, 140, len(chosen)), unit="D")
    second["onset_date"] = second["onset_date"].clip(upper=period_end)
    second["first_report_date"] = second["onset_date"] + pd.to_timedelta(
        rng.integers(1, 8, len(chosen)), unit="D")
    second["hospitalized"] = "No"
    second["case_status"] = "Confirmed"

    cases = pd.concat([cases, second], ignore_index=True)
    cases["true_case_id"] = [f"T{i:04d}" for i in range(1, len(cases) + 1)]
    return cases


def make_reports(cases):
    """Turn each true case into one or more reports, the way they would arrive."""
    first = cases.copy()
    first["report_date"] = first["first_report_date"]
    first["reporting_facility"] = pick(config.FACILITIES, [3, 1, 2, 2, 2], len(first))

    has_dup = rng.random(len(cases)) < config.DUPLICATE_RATE
    dups = cases[has_dup].copy()
    dups["report_date"] = dups["first_report_date"] + pd.to_timedelta(
        rng.integers(1, 7, len(dups)), unit="D")
    dups["reporting_facility"] = "Regional Reference Lab"
    # Lab reports often leave hospitalization blank
    dups.loc[rng.random(len(dups)) < 0.5, "hospitalized"] = "Unknown"

    # When a second report is coming, the first one often knows less:
    # still "Suspect" before the lab confirms, or hospitalization not yet known.
    early = first.index[has_dup]
    upgrade = early[(first.loc[early, "case_status"] == "Confirmed").to_numpy()
                    & (rng.random(len(early)) < 0.5)]
    first.loc[upgrade, "case_status"] = "Suspect"
    later_hosp = early[(first.loc[early, "hospitalized"] == "Yes").to_numpy()
                       & (rng.random(len(early)) < 0.5)]
    first.loc[later_hosp, "hospitalized"] = "Unknown"

    reports = pd.concat([first, dups], ignore_index=True)
    # Anything "sent" after our extract date has not reached us yet
    reports = reports[reports["report_date"] <= pd.Timestamp(config.EXTRACT_DATE)]
    reports = reports.sample(frac=1, random_state=config.RANDOM_SEED).reset_index(drop=True)
    reports["report_id"] = [f"R{i:05d}" for i in range(1, len(reports) + 1)]
    return reports


def to_messy_text(reports):
    """Write reports out as text the way a reporting system export looks, then plant problems."""
    n = len(reports)

    def style(values, styles):
        return [styles[v][rng.integers(len(styles[v]))] for v in values]

    raw = pd.DataFrame({
        "report_id": reports["report_id"],
        "patient_id": reports["patient_id"],
        "disease": reports["disease"],
        "case_status": reports["case_status"],
        "onset_date": reports["onset_date"].dt.strftime("%Y-%m-%d"),
        "report_date": reports["report_date"].dt.strftime("%Y-%m-%d"),
        "age": reports["age"].astype(int).astype(str),
        "sex": style(reports["sex"], SEX_STYLES),
        "district": reports["district"],
        "reporting_facility": reports["reporting_facility"],
        "hospitalized": style(reports["hospitalized"], YN_STYLES),
        "died": style(reports["died"], YN_STYLES),
    }).astype(object)

    # Give each problem to a separate set of reports (at most one problem each)
    problem = np.full(n, "none", dtype=object)
    order = rng.permutation(n)
    pos = 0
    for name, share in {**KEEP_PROBLEMS, **REJECT_PROBLEMS}.items():
        k = int(round(share * n))
        problem[order[pos:pos + k]] = name
        pos += k

    aliases_for = {}
    for alias, canonical in config.DISEASE_ALIASES.items():
        if alias != canonical.lower():
            aliases_for.setdefault(canonical, []).append(alias)

    for i in range(n):
        p = problem[i]
        if p == "disease_spelling_variant":
            options = aliases_for[raw.at[i, "disease"]] + [raw.at[i, "disease"].upper()]
            raw.at[i, "disease"] = f" {rng.choice(options).title()} " if rng.random() < 0.3 else rng.choice(options)
        elif p == "us_date_format":
            raw.at[i, "report_date"] = reports.at[i, "report_date"].strftime("%m/%d/%Y")
            raw.at[i, "onset_date"] = reports.at[i, "onset_date"].strftime("%m/%d/%Y")
        elif p == "missing_onset":
            raw.at[i, "onset_date"] = ""
        elif p == "onset_after_report":
            bad = reports.at[i, "report_date"] + pd.Timedelta(days=int(rng.integers(3, 21)))
            raw.at[i, "onset_date"] = bad.strftime("%Y-%m-%d")
        elif p == "age_out_of_range":
            raw.at[i, "age"] = str(rng.choice([150, 999, -4, 212]))
        elif p == "unknown_district":
            raw.at[i, "district"] = rng.choice(["", "Rivrside", "N/A"])
        elif p == "missing_disease":
            raw.at[i, "disease"] = ""
        elif p == "unrecognized_disease":
            raw.at[i, "disease"] = rng.choice(["TBD", "Unknown illness", "GI illness"])
        elif p == "bad_report_date":
            raw.at[i, "report_date"] = rng.choice(["2026-13-05", "unknown", "31/31/2026"])
        elif p == "missing_patient_id":
            raw.at[i, "patient_id"] = ""

    answer_key = pd.DataFrame({
        "report_id": reports["report_id"],
        "true_case_id": reports["true_case_id"],
        "true_disease": reports["disease"],
        "injected_problem": problem,
        "expected_outcome": np.where(np.isin(problem, list(REJECT_PROBLEMS)), "reject", "keep"),
    })
    return raw, answer_key


def main():
    cases = make_true_cases()
    reports = make_reports(cases)
    raw, answer_key = to_messy_text(reports)

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw.to_csv(config.RAW_FILE, index=False)
    answer_key.to_csv(config.ANSWER_KEY_FILE, index=False)

    print(f"Wrote {len(raw)} synthetic reports covering "
          f"{answer_key['true_case_id'].nunique()} true cases to {config.RAW_FILE.name}")
    print("Planted problems:")
    print(answer_key["injected_problem"].value_counts().drop("none").to_string())


if __name__ == "__main__":
    main()
