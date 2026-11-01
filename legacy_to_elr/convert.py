"""
Legacy lab exports in, HL7 2.5.1 ELR messages out.

Steps (each is a function below):
  1. extract        read each source file exactly as received
  2. harmonize      use that source's field map to rename its columns into one
                    common layout, parsing its date and name formats
  3. code           translate each source's local values into standard codes:
                    tests -> LOINC, results and specimens -> SNOMED CT,
                    sex -> HL7 codes; look up ordering facilities in the directory
  4. classify       decide each row's fate: rejected, needs mapping, duplicate,
                    not reportable, or ready to send
  5. build          turn each ready row into an HL7 ORU^R01 message
  6. validate       re-read every message and check it against our rules
  7. report         messages, a row-by-row log, the follow-up lists, and a
                    gap report for each source

Run:  python convert.py
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import config
from hl7_builder import fld, get, message, parse, segment, sub

TZ = ZoneInfo(config.TIMEZONE)
EXPECTED_SEGMENTS = ["MSH", "SFT", "PID", "ORC", "OBR", "OBX", "SPM"]


def load_tables():
    names = ["sources", "field_map", "value_map", "local_test_map", "loinc_tests",
             "snomed_codes", "facility_directory", "elr_requirements"]
    return {n: pd.read_csv(config.MAPPINGS_DIR / f"{n}.csv", dtype=str, keep_default_na=False)
            for n in names}


def norm(series):
    """Lowercase and trim, for matching local values against the maps."""
    return series.fillna("").astype(str).str.strip().str.lower()


# ---------------------------------------------------------------------------
# 1. Extract
# ---------------------------------------------------------------------------
class ConversionError(Exception):
    """A problem with the input file itself, explained in plain language."""


def extract(path, delimiter):
    try:
        raw = pd.read_csv(path, sep=delimiter, dtype=str, keep_default_na=False)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as err:
        raise ConversionError(f"The file could not be read as a table: {err}") from err
    raw.insert(0, "row_number", range(1, len(raw) + 1))
    return raw


def check_columns(raw, source_id, field_map, source_name):
    """Stop early, with a clear message, if the file is not this source's layout."""
    expected = field_map.loc[(field_map["source_id"] == source_id)
                             & (field_map["source_column"] != ""), "source_column"]
    missing = [c for c in expected if c not in raw.columns]
    if missing:
        raise ConversionError(
            f"This file does not look like a {source_name} export. Missing columns: "
            f"{', '.join(missing)}. Check that you picked the right source.")
    if raw.empty:
        raise ConversionError("The file has column headings but no rows.")


# ---------------------------------------------------------------------------
# 2. Harmonize
# ---------------------------------------------------------------------------
def split_name(text, style):
    text = text.fillna("")
    if style == "last_comma_first":
        parts = text.str.split(",", n=1, expand=True)
    elif style == "last_caret_first":
        parts = text.str.split("^", n=1, expand=True)
    elif style == "first_last":
        parts = text.str.rsplit(" ", n=1, expand=True)[[1, 0]]
        parts.columns = [0, 1]
    else:
        raise ValueError(f"unknown name style {style}")
    parts = parts.reindex(columns=[0, 1])
    clean = lambda s: s.fillna("").str.strip().replace("", None)
    return clean(parts[0]), clean(parts[1])


def harmonize(raw, source_id, field_map):
    out = pd.DataFrame({"source_id": source_id, "row_number": raw["row_number"]})
    for m in field_map[field_map["source_id"] == source_id].itertuples():
        if m.constant:
            out[m.common_field] = m.constant
            continue
        text = raw[m.source_column].str.strip().replace("", None)
        kind, _, pattern = m.format.partition(":")
        if kind in ("date", "datetime"):
            out[f"{m.common_field}_raw"] = text
            parsed = pd.to_datetime(text, format=pattern, errors="coerce")
            # Python's date reader is lenient: "20261345" can sneak through as
            # 2026-01-03 04:05. A date only counts if it converts back to
            # exactly the text we started with.
            round_trip = parsed.dt.strftime(pattern) == text
            out[m.common_field] = parsed.where(round_trip)
            out[f"{m.common_field}_has_time"] = kind == "datetime"
        elif kind == "name":
            prefix = "patient" if m.common_field.startswith("patient") else "provider"
            out[f"{prefix}_last"], out[f"{prefix}_first"] = split_name(text, pattern)
        else:
            out[m.common_field] = text
    return out


# ---------------------------------------------------------------------------
# 3. Code
# ---------------------------------------------------------------------------
def code(df, t):
    df = df.copy()
    for col in ["race", "ethnicity", "patient_phone", "provider_id", "specimen_local",
                "dob", "street", "city", "state"]:
        if col not in df:
            df[col] = None

    vm = t["value_map"]
    lookup = {(r.source_id, r.common_field, r.local_value): r.standard_value for r in vm.itertuples()}

    def translate(field):
        keys = norm(df[field])
        return [lookup.get((s, field, k)) for s, k in zip(df["source_id"], keys)]

    df["sex"] = translate("sex")
    df["result_code"] = translate("result_local")
    df["specimen_code"] = translate("specimen_local")

    tests = {(r.source_id, r.local_test): r.loinc_code for r in t["local_test_map"].itertuples()}
    df["local_test"] = df["local_test"].str.strip()
    df["loinc_code"] = [tests.get((s, k)) for s, k in zip(df["source_id"], norm(df["local_test"]))]

    loinc = t["loinc_tests"].set_index("loinc_code")
    snomed = t["snomed_codes"].set_index("snomed_code")["display"]
    df["loinc_name"] = df["loinc_code"].map(loinc["long_name"])

    # A LOINC code already names its specimen ("...in Serum"), so when a source
    # has no specimen column, the specimen type is derived from the test code.
    derive = df["specimen_local"].isna() & df["loinc_code"].notna()
    df.loc[derive, "specimen_code"] = df.loc[derive, "loinc_code"].map(loinc["default_specimen"])
    df["specimen_derived"] = derive
    df["result_display"] = df["result_code"].map(snomed)
    df["specimen_display"] = df["specimen_code"].map(snomed)

    directory = t["facility_directory"].assign(key=lambda d: norm(d["facility_name"]))
    directory = directory.drop(columns="facility_name").add_prefix("facility_")
    df["facility_key"] = norm(df["ordering_facility"])
    df = df.merge(directory, how="left", left_on="facility_key", right_on="facility_key")
    df["facility_phone"] = df["facility_phone_number"]

    digits = df["patient_phone"].fillna("").str.replace(r"\D", "", regex=True)
    df["patient_phone_area"] = digits.str[:3].replace("", None)
    df["patient_phone_local"] = digits.str[3:].replace("", None)
    return df


# ---------------------------------------------------------------------------
# 4. Classify
# ---------------------------------------------------------------------------
def classify(df):
    """
    Order matters. A row is checked for problems in this order, and the first
    one found decides its fate:
      rejected       an essential piece is missing or unreadable; ask the source
      needs_mapping  a local code is not in our maps yet; a mapping task
      duplicate      already sent from an earlier row
      not_reportable a valid result the state does not require (e.g. negative)
      ready          build and send
    """
    rejected = {
        "missing patient ID": df["patient_id"].isna(),
        "missing patient name": df["patient_last"].isna(),
        "missing collection date": df["collected_at_raw"].isna(),
        "unreadable collection date": df["collected_at_raw"].notna() & df["collected_at"].isna(),
        "unreadable result date": df["resulted_at_raw"].notna() & df["resulted_at"].isna(),
        "missing ordering facility": df["ordering_facility"].isna(),
        "ordering facility not in directory": df["ordering_facility"].notna() & df["facility_street"].isna(),
    }
    mapping = {
        "test code not mapped": df["local_test"].notna() & df["loinc_code"].isna(),
        "result value not mapped": df["result_local"].notna() & df["result_code"].isna(),
        "specimen value not mapped": df["specimen_local"].notna() & df["specimen_code"].isna(),
    }

    def first_reason(rules):
        hits = pd.DataFrame(rules)
        return hits.apply(lambda r: "; ".join(hits.columns[r.to_numpy()]), axis=1)

    df = df.copy()
    df["reason"] = first_reason(rejected)
    df["status"] = "ready"
    df.loc[df["reason"] != "", "status"] = "rejected"

    open_rows = df["status"] == "ready"
    map_reason = first_reason(mapping)
    needs = open_rows & (map_reason != "")
    df.loc[needs, "status"] = "needs_mapping"
    df.loc[needs, "reason"] = map_reason[needs]

    open_rows = df["status"] == "ready"
    dup = open_rows & df[open_rows].duplicated(config.DUPLICATE_KEY, keep="first").reindex(df.index, fill_value=False)
    df.loc[dup, ["status", "reason"]] = ["duplicate", "same result already sent"]

    open_rows = df["status"] == "ready"
    not_rep = open_rows & ~df["result_code"].isin(config.REPORTABLE_RESULT_CODES)
    df.loc[not_rep, "status"] = "not_reportable"
    df.loc[not_rep, "reason"] = "result not reportable: " + df.loc[not_rep, "result_display"].fillna("")
    return df


# ---------------------------------------------------------------------------
# 5. Build
# ---------------------------------------------------------------------------
def ts(value, has_time):
    """HL7 date/time. Times carry a time zone offset; date-only values do not."""
    if pd.isna(value):
        return ""
    if has_time is True:
        return value.tz_localize(TZ).strftime("%Y%m%d%H%M%z")
    return value.strftime("%Y%m%d")


def build_message(r, src, sent_at, control_id):
    filler = fld(r.record_id, src.source_id, src.clia, "CLIA")
    provider = fld(r.provider_id, r.provider_last, r.provider_first)
    test = fld(r.loinc_code, r.loinc_name, "LN", r.local_test, r.local_test, "L")
    if r.specimen_derived:
        specimen = fld(r.specimen_code, r.specimen_display, "SCT")
    else:
        specimen = fld(r.specimen_code, r.specimen_display, "SCT", r.specimen_local, r.specimen_local, "L")
    collected = ts(r.collected_at, r.collected_at_has_time)
    resulted = ts(r.resulted_at, r.resulted_at_has_time)
    phone = (fld("", "PRN", "PH", "", "1", r.patient_phone_area, r.patient_phone_local)
             if isinstance(r.patient_phone_local, str) else "")

    segments = [
        segment("MSH", {3: fld(*config.SENDING_APPLICATION), 4: fld(src.name, src.clia, "CLIA"),
                        5: fld(*config.RECEIVING_APPLICATION), 6: fld(*config.RECEIVING_FACILITY),
                        7: sent_at, 9: fld("ORU", "R01", "ORU_R01"), 10: control_id,
                        11: config.PROCESSING_ID, 12: "2.5.1", 15: "NE", 16: "NE",
                        21: fld(*config.MESSAGE_PROFILE)}),
        segment("SFT", {1: fld(config.SOFTWARE_NAME), 2: config.SOFTWARE_VERSION,
                        3: "legacy-to-elr", 4: config.SOFTWARE_VERSION}),
        segment("PID", {1: "1", 3: fld(r.patient_id, "", "", sub(src.source_id, src.oid, "ISO"), "MR"),
                        5: fld(r.patient_last, r.patient_first, "", "", "", "", "L"),
                        7: ts(r.dob, False), 8: r.sex,
                        11: fld(r.street, "", r.city, r.state, r.zip, "USA", "H"), 13: phone}),
        segment("ORC", {1: "RE", 3: filler, 12: provider, 21: fld(r.ordering_facility),
                        22: fld(r.facility_street, "", r.facility_city, config.STATE, r.facility_zip, "USA", "B"),
                        23: fld("", "WPN", "PH", "", "1", r.facility_phone_area, r.facility_phone_number)}),
        segment("OBR", {1: "1", 3: filler, 4: test, 7: collected, 16: provider, 22: resulted, 25: "F"}),
        segment("OBX", {1: "1", 2: "CWE", 3: test,
                        5: fld(r.result_code, r.result_display, "SCT", r.result_local, r.result_local, "L"),
                        11: "F", 14: collected, 19: resulted,
                        23: fld(src.name, "L", "", "", "", sub("CLIA", config.CLIA_OID, "ISO"), "XX", "", "", src.clia),
                        24: fld(src.street, "", src.city, config.STATE, src.zip, "USA", "B")}),
        segment("SPM", {1: "1", 2: fld("", sub(r.record_id, src.source_id, src.clia, "CLIA")),
                        4: specimen, 17: fld(collected)}),
    ]
    return message(segments)


# ---------------------------------------------------------------------------
# 6. Validate
# ---------------------------------------------------------------------------
def validate_message(msg, t):
    p = parse(msg)
    errors = []
    names = [name for name, _ in p]
    if names != EXPECTED_SEGMENTS:
        errors.append(f"segments {names} != {EXPECTED_SEGMENTS}")
    literals = {"MSH-9": "ORU^R01^ORU_R01", "MSH-12": "2.5.1",
                "MSH-21": "^".join(config.MESSAGE_PROFILE)}
    errors += [f"{loc} should be {want}" for loc, want in literals.items() if get(p, loc) != want]

    required = t["elr_requirements"]
    for req in required[required["usage"] == "R"].itertuples():
        if not get(p, req.check_location):
            errors.append(f"required {req.check_location} ({req.description}) is empty")

    snomed = t["snomed_codes"]
    allowed = {
        "OBX-3": (set(t["loinc_tests"]["loinc_code"]), "LN"),
        "OBX-5": (set(snomed.loc[snomed["category"] == "result", "snomed_code"]), "SCT"),
        "SPM-4": (set(snomed.loc[snomed["category"] == "specimen", "snomed_code"]), "SCT"),
    }
    for loc, (codes, system) in allowed.items():
        if get(p, f"{loc}.1") not in codes or get(p, f"{loc}.3") != system:
            errors.append(f"{loc} code not in the {system} table")

    collected, resulted, dob = get(p, "OBR-7")[:8], get(p, "OBR-22")[:8], get(p, "PID-7")
    if collected and resulted and collected > resulted:
        errors.append("result reported before specimen was collected")
    if dob and collected and dob > collected:
        errors.append("birth date after collection date")
    return errors


# ---------------------------------------------------------------------------
# 7. Report
# ---------------------------------------------------------------------------
FIELD_NAMES = {
    "MSH-3": "Sending application", "MSH-4": "Sending facility (name^CLIA^CLIA)",
    "MSH-5": "Receiving application", "MSH-6": "Receiving facility", "MSH-7": "Message date/time",
    "MSH-9": "Message type (lab result)", "MSH-10": "Message control ID (unique)",
    "MSH-11": "Processing ID", "MSH-12": "HL7 version", "MSH-15": "Accept acknowledgment",
    "MSH-16": "Application acknowledgment", "MSH-21": "Message profile (national ELR guide)",
    "SFT-1": "Software organization", "SFT-2": "Software version", "SFT-3": "Software name",
    "SFT-4": "Software build ID", "PID-1": "Set ID", "PID-3": "Patient ID^^^assigning authority^type",
    "PID-5": "Patient name (last^first)", "PID-7": "Date of birth", "PID-8": "Sex",
    "PID-11": "Patient address", "PID-13": "Patient phone", "ORC-1": "Order control (RE = results)",
    "ORC-3": "Filler order number (the lab's accession)", "ORC-12": "Ordering provider",
    "ORC-21": "Ordering facility", "ORC-22": "Ordering facility address",
    "ORC-23": "Ordering facility phone", "OBR-1": "Set ID", "OBR-3": "Filler order number",
    "OBR-4": "Test (LOINC^name^LN^local code^local name^L)", "OBR-7": "Specimen collected",
    "OBR-16": "Ordering provider", "OBR-22": "Result reported", "OBR-25": "Result status (F = final)",
    "OBX-1": "Set ID", "OBX-2": "Value type (CWE = coded)", "OBX-3": "Test performed (LOINC)",
    "OBX-5": "Result (SNOMED CT^name^SCT^local value^local value^L)",
    "OBX-11": "Result status (F = final)", "OBX-14": "Observation date/time",
    "OBX-19": "Analysis date/time", "OBX-23": "Performing lab (name and CLIA number)",
    "OBX-24": "Performing lab address", "SPM-1": "Set ID", "SPM-2": "Specimen ID",
    "SPM-4": "Specimen type (SNOMED CT)", "SPM-17": "Specimen collection date/time",
}


SEGMENT_NAMES = {
    "MSH": "Message header", "SFT": "Software", "PID": "Patient", "ORC": "Order",
    "OBR": "Test ordered", "OBX": "Result", "SPM": "Specimen",
}


def explain_rows(msg):
    """[(segment, segment description, [(field id, label, value), ...]), ...]"""
    out = []
    for name, fields in parse(msg):
        rows = [(f"{name}-{i}", FIELD_NAMES.get(f"{name}-{i}", ""), value)
                for i, value in enumerate(fields)
                if i > 0 and value and not (name == "MSH" and i < 3)]
        out.append((name, SEGMENT_NAMES.get(name, ""), rows))
    return out


def explain(msg):
    lines = ["ONE SYNTHETIC ELR MESSAGE, FIELD BY FIELD", ""]
    for name, _, rows in explain_rows(msg):
        lines.append(f"--- {name} ---")
        lines += [f"{fid:<7} {label:<45} {value}" for fid, label, value in rows]
        lines.append("")
    return "\n".join(lines)


def gap_report(df, t):
    fm, req = t["field_map"], t["elr_requirements"]
    derived = {
        "loinc_code": ("local_test", "mapped from local test code"),
        "result_code": ("result_local", "mapped from local result value"),
        "specimen_code": ("specimen_local", "mapped from local specimen value"),
        "facility_street": ("ordering_facility", "looked up in facility directory"),
        "facility_phone": ("ordering_facility", "looked up in facility directory"),
    }
    rows = []
    present = set(df["source_id"])
    for sid in [s for s in t["sources"]["source_id"] if s in present]:
        provided = {}
        for m in fm[fm["source_id"] == sid].itertuples():
            how = f"set once for this source ({m.constant})" if m.constant else f"column '{m.source_column}'"
            if m.format.startswith("name:"):
                prefix = "patient" if m.common_field.startswith("patient") else "provider"
                provided[f"{prefix}_last"] = provided[f"{prefix}_first"] = how + ", split"
            else:
                provided[m.common_field] = how
        part = df[df["source_id"] == sid]
        for r in req.itertuples():
            f, note = r.common_field, ""
            if f in derived:
                src_field, desc = derived[f]
                how = f"{desc} ({provided[src_field]})" if src_field in provided else None
                if f == "specimen_code" and how is None and "local_test" in provided:
                    how, note = "derived from the LOINC test code", "confirm with the lab"
            else:
                how = provided.get(f)
            if f == "dob" and how is None and "age" in provided:
                note = "only age is collected; a birth date is never invented from an age"
            complete = part[f].notna().mean() if f in part else 0.0
            status = "NOT COLLECTED" if how is None else ("OK" if complete == 1 else "PARTIAL")
            rows.append({"source_id": sid, "field": r.description, "hl7_location": r.check_location,
                         "usage": r.usage, "how_obtained": how or "", "percent_complete": round(100 * complete),
                         "status": status, "note": note})
    return pd.DataFrame(rows)


def gap_text(gaps):
    lines = ["GAP REPORT: what each source cannot yet supply (synthetic data)", "",
             "Usage: R = required, RE = required if known. Usage levels are prototype",
             "assumptions; confirm them in the HL7 ELR 2.5.1 guide and your state's guide.", ""]
    for sid, part in gaps.groupby("source_id", sort=False):
        lines.append(f"[{sid}]")
        issues = part[part["status"] != "OK"]
        if issues.empty:
            lines.append("    no gaps")
        for r in issues.itertuples():
            extra = f" ({r.note})" if r.note else ""
            lines.append(f"    {r.status:<14} {r.usage:<3} {r.field} [{r.hl7_location}], "
                         f"{r.percent_complete}% complete{extra}")
        lines.append("")
    return "\n".join(lines)


def convert(inputs, out_dir, verbose=True):
    """
    inputs: list of (source_id, path to that source's export file).
    Writes every output into out_dir and returns (rows, messages, summary).
    """
    t = load_tables()
    sources = {s.source_id: s for s in t["sources"].itertuples()}
    frames = []
    for source_id, path in inputs:
        src = sources[source_id]
        raw = extract(path, src.delimiter)
        check_columns(raw, source_id, t["field_map"], src.name)
        frames.append(harmonize(raw, source_id, t["field_map"]))
    df = classify(code(pd.concat(frames, ignore_index=True), t))

    sent_at = datetime.now(TZ).strftime("%Y%m%d%H%M%S%z")
    messages = []
    df["control_id"] = ""
    for i, r in df[df["status"] == "ready"].iterrows():
        rt = next(df.loc[[i]].itertuples())
        control_id = f"{r.source_id}-{r.record_id}-{r.row_number}"
        msg = build_message(rt, sources[r.source_id], sent_at, control_id)
        errors = validate_message(msg, t)
        if errors:
            df.loc[i, ["status", "reason"]] = ["failed_validation", "; ".join(errors)]
        else:
            df.loc[i, ["status", "control_id"]] = ["sent", control_id]
            messages.append(msg)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "elr_messages.hl7").write_text("\n".join(messages), newline="")
    (out / "elr_messages_readable.txt").write_text(
        "\n\n".join(m.replace("\r", "\n").strip() for m in messages) + "\n")
    if messages:
        (out / "sample_message_explained.txt").write_text(explain(messages[0]))

    log_cols = ["source_id", "row_number", "record_id", "status", "reason", "local_test",
                "loinc_code", "result_local", "result_code", "control_id"]
    df[log_cols].to_csv(out / "conversion_log.csv", index=False)
    df[df["status"] == "rejected"][["source_id", "row_number", "record_id", "reason"]].to_csv(
        out / "quarantine_for_followup.csv", index=False)

    needs_mapping(df).to_csv(out / "needs_mapping.csv", index=False)

    gaps = gap_report(df, t)
    gaps.to_csv(out / "gap_report.csv", index=False)
    (out / "gap_report.txt").write_text(gap_text(gaps))

    counts = df.groupby(["source_id", "status"]).size().unstack(fill_value=0)
    summary = "\n".join([
        "CONVERSION SUMMARY (synthetic data)", "",
        counts.to_string(), "",
        f"Messages written: {len(messages)}",
        f"Messages failing our validation: {(df['status'] == 'failed_validation').sum()}",
        f"Specimen types derived from test code: {int(df.loc[df['status'] == 'sent', 'specimen_derived'].sum())}",
    ])
    (out / "run_summary.txt").write_text(summary + "\n")
    if verbose:
        print(summary)
        print()
        print(gap_text(gaps))
    return df, messages, summary


def needs_mapping(df):
    """One line per local value that is not in the maps yet, with how many rows it blocks."""
    needs = df[df["status"] == "needs_mapping"]
    parts = [
        needs[needs["reason"].str.contains("test code")].assign(kind="test code", local_value=needs["local_test"]),
        needs[needs["reason"].str.contains("result value")].assign(kind="result value", local_value=needs["result_local"]),
        needs[needs["reason"].str.contains("specimen value")].assign(kind="specimen value", local_value=needs["specimen_local"]),
    ]
    table = pd.concat(parts) if any(len(p) for p in parts) else needs.assign(kind=None, local_value=None)
    return (table.groupby(["source_id", "kind", "local_value"]).size().rename("rows").reset_index())


def run():
    """Convert every configured source's sample file (the command-line path)."""
    t = load_tables()
    inputs = [(s.source_id, config.LEGACY_DIR / s.file) for s in t["sources"].itertuples()]
    df, messages, _ = convert(inputs, config.OUTPUT_DIR)
    return df, messages


if __name__ == "__main__":
    run()
