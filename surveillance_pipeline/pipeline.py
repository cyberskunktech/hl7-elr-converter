"""
The pipeline: raw case reports in, clean case counts out.

Each step is one function below:
  1. extract      read the raw file exactly as received (nothing changed)
  2. standardize  make the same thing always look the same
  3. validate     set aside reports we cannot use, with the reason;
                  fix and flag the ones we can
  4. deduplicate  turn reports (paperwork) into cases (people)
  5. enrich       add epi week, age group, and reporting delay
  6. load         save everything to the database, with a run log
  7. summarize    weekly counts, a chart, and a data quality report

Run:  python pipeline.py
"""
import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd

import config

# ---------------------------------------------------------------------------
# Epi weeks. Use the established, tested package if it is installed
# (pip install epiweeks). The fallback below follows the same CDC rule and is
# checked against known dates in tests/test_pipeline.py.
# ---------------------------------------------------------------------------
try:
    from epiweeks import Week

    def mmwr_week(d):
        d = d.date() if hasattr(d, "date") else d
        w = Week.fromdate(d, system="cdc")
        return w.year, w.week

    EPIWEEK_SOURCE = "epiweeks package"
except ImportError:
    def _week1_start(year):
        # MMWR week 1 is the Sunday-to-Saturday week that contains January 4
        jan4 = date(year, 1, 4)
        return jan4 - timedelta(days=(jan4.weekday() + 1) % 7)

    def mmwr_week(d):
        d = d.date() if hasattr(d, "date") else d
        year = d.year
        if d < _week1_start(year):
            year -= 1
        elif d >= _week1_start(year + 1):
            year += 1
        return year, (d - _week1_start(year)).days // 7 + 1

    EPIWEEK_SOURCE = "built-in fallback"


def week_start(dates):
    """The Sunday that begins each date's MMWR week."""
    return dates - pd.to_timedelta((dates.dt.dayofweek + 1) % 7, unit="D")


# ---------------------------------------------------------------------------
# 1. Extract
# ---------------------------------------------------------------------------
def extract(path):
    """Read every column as plain text so nothing gets silently reinterpreted."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# ---------------------------------------------------------------------------
# 2. Standardize
# ---------------------------------------------------------------------------
def clean_text(series):
    """Trim spaces; treat empty text as missing."""
    s = series.str.strip()
    return s.where(s != "", None)


def parse_dates(series):
    """Accept only the formats listed in config. Anything else becomes missing."""
    result = pd.to_datetime(series, format=config.DATE_FORMATS[0], errors="coerce")
    for fmt in config.DATE_FORMATS[1:]:
        result = result.fillna(pd.to_datetime(series, format=fmt, errors="coerce"))
    return result


def standardize(raw):
    df = pd.DataFrame({"report_id": clean_text(raw["report_id"])})
    df["patient_id"] = clean_text(raw["patient_id"])

    disease_text = clean_text(raw["disease"])
    df["disease_raw"] = disease_text
    df["disease"] = disease_text.str.lower().map(config.DISEASE_ALIASES)

    df["case_status"] = clean_text(raw["case_status"]).str.lower().map(config.CASE_STATUS_ALIASES)

    for col in ["onset_date", "report_date"]:
        text = clean_text(raw[col])
        df[f"{col}_raw"] = text
        df[col] = parse_dates(text)

    age_text = clean_text(raw["age"])
    df["age_raw"] = age_text
    df["age"] = pd.to_numeric(age_text, errors="coerce")

    # Unknown values stay missing for now so a duplicate report can fill them in
    df["sex"] = clean_text(raw["sex"]).str.lower().map(config.SEX_ALIASES)
    district = clean_text(raw["district"])
    df["district"] = district.where(district.isin(list(config.DISTRICTS)), None)
    df["district_raw"] = district
    df["reporting_facility"] = clean_text(raw["reporting_facility"])
    for col in ["hospitalized", "died"]:
        df[col] = clean_text(raw[col]).str.lower().map(config.YES_NO_ALIASES)
    return df


# ---------------------------------------------------------------------------
# 3. Validate
# ---------------------------------------------------------------------------
def validate(df):
    """
    Two kinds of problems:
      - reject: the report cannot be used (no disease, no report date, no
        patient ID). It goes to a follow-up list with the reason.
      - flag: the report is usable, but one field was wrong, so we blank that
        field and note why. A typo in age should not make a case disappear.
    """
    extract_date = pd.Timestamp(config.EXTRACT_DATE)
    reject_rules = {
        "missing disease": df["disease_raw"].isna(),
        "unrecognized disease": df["disease_raw"].notna() & df["disease"].isna(),
        "missing report date": df["report_date_raw"].isna(),
        "unreadable report date": df["report_date_raw"].notna() & df["report_date"].isna(),
        "report date after extract date": df["report_date"] > extract_date,
        "missing patient ID": df["patient_id"].isna(),
    }
    flag_rules = {
        "unreadable onset date": df["onset_date_raw"].notna() & df["onset_date"].isna(),
        "onset after report date": df["onset_date"] > df["report_date"],
        "age out of range": df["age"].notna() & ~df["age"].between(config.AGE_MIN, config.AGE_MAX),
        "unreadable age": df["age_raw"].notna() & df["age"].isna(),
        "unknown district": df["district"].isna(),
    }

    def reasons(rules):
        hits = pd.DataFrame(rules)
        return hits.apply(lambda row: "; ".join(hits.columns[row.to_numpy()]), axis=1)

    df = df.copy()
    df["reject_reason"] = reasons(reject_rules)
    df["quality_flags"] = reasons(flag_rules)

    # Apply the fixes for flagged fields
    df.loc[flag_rules["onset after report date"], "onset_date"] = pd.NaT
    df.loc[flag_rules["age out of range"], "age"] = None

    rejected = df[df["reject_reason"] != ""]
    accepted = df[df["reject_reason"] == ""].drop(columns=["reject_reason"])
    return accepted, rejected


# ---------------------------------------------------------------------------
# 4. Deduplicate
# ---------------------------------------------------------------------------
def strongest(series, rank):
    """Pick the strongest value, e.g. Confirmed over Suspect, Yes over No."""
    values = series.dropna()
    return max(values, key=rank.get) if len(values) else None


def deduplicate(df):
    """
    One person with one illness can generate several reports (clinic, lab,
    hospital). We group reports for the same patient and disease that arrive
    within DEDUP_WINDOW_DAYS of each other into a single case.
    """
    df = df.sort_values(["patient_id", "disease", "report_date", "report_id"]).copy()
    gap_days = df.groupby(["patient_id", "disease"])["report_date"].diff().dt.days
    starts_new_case = gap_days.isna() | (gap_days > config.DEDUP_WINDOW_DAYS)
    df["case_id"] = "CASE-" + starts_new_case.cumsum().astype(str).str.zfill(5)

    yes_no_rank = {"No": 1, "Yes": 2}
    g = df.groupby("case_id", sort=True)
    cases = pd.DataFrame({
        "patient_id": g["patient_id"].first(),
        "disease": g["disease"].first(),
        # Strongest classification across reports (a lab result upgrades a suspect case)
        "case_status": g["case_status"].agg(strongest, rank=config.CASE_STATUS_RANK),
        "onset_date": g["onset_date"].min(),
        "first_report_date": g["report_date"].min(),
        # Other fields: the first report that filled them in
        "age": g["age"].first(),
        "sex": g["sex"].first(),
        "district": g["district"].first(),
        # Outcomes: if any report says Yes, the answer is Yes
        "hospitalized": g["hospitalized"].agg(strongest, rank=yes_no_rank),
        "died": g["died"].agg(strongest, rank=yes_no_rank),
        "n_reports": g["report_id"].count(),
        "quality_flags": g["quality_flags"].agg(
            lambda s: "; ".join(sorted({f for v in s for f in v.split("; ") if f}))),
    }).reset_index()

    fill_unknown = ["case_status", "sex", "district", "hospitalized", "died"]
    cases[fill_unknown] = cases[fill_unknown].fillna("Unknown")
    links = df[["report_id", "case_id"]]
    return cases, links


# ---------------------------------------------------------------------------
# 5. Enrich
# ---------------------------------------------------------------------------
def enrich(cases):
    cases = cases.copy()
    # Count each case in the week the illness started. If onset is unknown,
    # fall back to the report date (CDC uses a similar "earliest date" idea).
    cases["event_date"] = cases["onset_date"].fillna(cases["first_report_date"])
    cases["event_date_source"] = cases["onset_date"].notna().map({True: "onset", False: "report"})

    weeks = {d: mmwr_week(d) for d in cases["event_date"].unique()}
    cases["mmwr_year"] = cases["event_date"].map(lambda d: weeks[d][0])
    cases["mmwr_week"] = cases["event_date"].map(lambda d: weeks[d][1])
    cases["week_start"] = week_start(cases["event_date"])

    cases["age_group"] = pd.cut(cases["age"], bins=config.AGE_BINS, labels=config.AGE_LABELS,
                                right=False).astype(object).fillna("Unknown")
    cases["report_delay_days"] = (cases["first_report_date"] - cases["onset_date"]).dt.days
    return cases


# ---------------------------------------------------------------------------
# 6. Load
# ---------------------------------------------------------------------------
def as_text_dates(df):
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    return out


def load(raw, rejected, cases, links, run_info):
    """
    Rebuild the tables on every run, so running twice never double-counts.
    The raw table keeps the original text exactly, so any number can be traced
    back to the report it came from.
    """
    conn = sqlite3.connect(config.DB_PATH)
    try:
        raw.to_sql("raw_reports", conn, if_exists="replace", index=False)
        as_text_dates(rejected[["report_id", "reject_reason"]]).to_sql(
            "rejected_reports", conn, if_exists="replace", index=False)
        as_text_dates(cases).to_sql("cases", conn, if_exists="replace", index=False)
        links.to_sql("case_reports", conn, if_exists="replace", index=False)
        pd.DataFrame([run_info]).to_sql("pipeline_runs", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 7. Summarize
# ---------------------------------------------------------------------------
def weekly_counts(cases):
    """Case counts for every week and disease, including weeks with zero cases."""
    weeks = pd.date_range(week_start(pd.Series([pd.Timestamp(config.PERIOD_START)]))[0],
                          week_start(pd.Series([pd.Timestamp(config.EXTRACT_DATE)]))[0],
                          freq="7D")
    grid = pd.MultiIndex.from_product([weeks, sorted(config.DISEASES)],
                                      names=["week_start", "disease"])
    counts = cases.groupby(["week_start", "disease"]).size().reindex(grid, fill_value=0)
    out = counts.rename("cases").reset_index()
    labels = out["week_start"].map(mmwr_week)
    out.insert(0, "mmwr_year", labels.str[0])
    out.insert(1, "mmwr_week", labels.str[1])
    cutoff = pd.Timestamp(config.EXTRACT_DATE) - pd.Timedelta(days=config.INCOMPLETE_WINDOW_DAYS)
    out["likely_incomplete"] = (out["week_start"] + pd.Timedelta(days=6)) > cutoff
    return out


def suppress_small(table):
    """Public version: show counts of 1 to 4 as '<5'."""
    small = (table > 0) & (table < config.SMALL_CELL_THRESHOLD)
    return table.astype(object).mask(small, f"<{config.SMALL_CELL_THRESHOLD}")


def plot_epi_curves(weekly, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    diseases = sorted(weekly["disease"].unique())
    fig, axes = plt.subplots(len(diseases), 1, figsize=(10, 2.1 * len(diseases)), sharex=True)
    for ax, disease in zip(axes, diseases):
        d = weekly[weekly["disease"] == disease]
        colors = ["#b8b8b8" if inc else "#2f6f8f" for inc in d["likely_incomplete"]]
        ax.bar(d["mmwr_week"], d["cases"], color=colors, width=0.8)
        ax.set_title(disease, loc="left", fontsize=10, fontweight="bold")
        ax.set_ylabel("Cases")
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_xlim(weekly["mmwr_week"].min() - 0.6, weekly["mmwr_week"].max() + 0.6)
    axes[-1].set_xlabel("MMWR week (2026), by illness onset")
    fig.suptitle("Weekly cases by disease  |  SYNTHETIC DATA, NOT REAL CASES\n"
                 "Grey bars: most recent weeks, likely incomplete because reports are still arriving",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def quality_report(raw, rejected, accepted, cases):
    n_raw, n_cases = len(raw), len(cases)
    flags = accepted["quality_flags"].str.split("; ").explode()
    flags = flags[flags != ""].value_counts()
    pct = lambda mask: f"{100 * mask.mean():.0f}%"

    lines = [
        "DATA QUALITY REPORT (synthetic data)",
        f"Run at {datetime.now():%Y-%m-%d %H:%M}, data extracted {config.EXTRACT_DATE}",
        "",
        f"Reports received:            {n_raw}",
        f"Rejected (need follow-up):   {len(rejected)}",
    ]
    lines += [f"    {reason}: {count}" for reason, count in
              rejected["reject_reason"].value_counts().items()]
    lines += [
        f"Accepted:                    {len(accepted)}",
        f"Accepted with a fix applied: {(accepted['quality_flags'] != '').sum()}",
    ]
    lines += [f"    {flag}: {count}" for flag, count in flags.items()]
    lines += [
        f"Duplicate reports merged:    {len(accepted) - n_cases}",
        f"Cases (unique illnesses):    {n_cases}",
        "",
        "Completeness (share of cases with a known value)",
        f"    onset date:      {pct(cases['onset_date'].notna())}",
        f"    age:             {pct(cases['age'].notna())}",
        f"    sex:             {pct(cases['sex'] != 'Unknown')}",
        f"    district:        {pct(cases['district'] != 'Unknown')}",
        f"    hospitalization: {pct(cases['hospitalized'] != 'Unknown')}",
        "",
        "Timeliness",
        f"    median days from onset to first report: {cases['report_delay_days'].median():.0f}",
        "",
        f"Epi weeks calculated with: {EPIWEEK_SOURCE}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run everything
# ---------------------------------------------------------------------------
def run():
    raw = extract(config.RAW_FILE)
    standardized = standardize(raw)
    accepted, rejected = validate(standardized)
    cases, links = deduplicate(accepted)
    cases = enrich(cases)

    run_info = {
        "run_timestamp": datetime.now().isoformat(timespec="seconds"),
        "source_file": config.RAW_FILE.name,
        "extract_date": config.EXTRACT_DATE,
        "reports_received": len(raw),
        "reports_rejected": len(rejected),
        "reports_accepted": len(accepted),
        "cases": len(cases),
    }
    load(raw, rejected, cases, links, run_info)

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    weekly = weekly_counts(cases)
    as_text_dates(weekly).to_csv(config.OUTPUT_DIR / "weekly_counts_by_disease.csv", index=False)

    by_district = pd.crosstab(cases["district"], cases["disease"])
    by_district.to_csv(config.OUTPUT_DIR / "cases_by_district_INTERNAL.csv")
    suppress_small(by_district).to_csv(config.OUTPUT_DIR / "cases_by_district_PUBLIC.csv")

    as_text_dates(rejected[["report_id", "reject_reason", "disease_raw", "report_date_raw"]]).to_csv(
        config.OUTPUT_DIR / "rejected_reports_for_followup.csv", index=False)
    plot_epi_curves(weekly, config.OUTPUT_DIR / "epi_curves.png")

    report = quality_report(raw, rejected, accepted, cases)
    (config.OUTPUT_DIR / "data_quality_report.txt").write_text(report + "\n")
    print(report)
    return cases, links, rejected


if __name__ == "__main__":
    run()
