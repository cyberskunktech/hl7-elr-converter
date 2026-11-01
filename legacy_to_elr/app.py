"""
Lab Reporting Desk: a local web interface for the legacy-to-ELR converter.

Run:   python app.py
Then open http://127.0.0.1:5000 in your browser.

The app runs only on this computer (127.0.0.1), and its pages load no outside
scripts, fonts, or images, so nothing leaves the machine.

PROTOTYPE: synthetic data only. Do not upload real patient information.
"""
import json
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import (Flask, abort, flash, redirect, render_template, request, send_file,
                   session, url_for)
from werkzeug.utils import secure_filename

import config
import convert
import generate_legacy_data
from hl7_builder import get, parse

RUNS_DIR = config.PROJECT_DIR / "runs"
RUN_ID = re.compile(r"^\d{8}-\d{6}-[A-Z]{3}-[0-9a-f]{4}$")
ALLOWED_EXTENSIONS = {".csv", ".txt"}
DOWNLOADS = {
    "elr_messages.hl7": "ELR messages (.hl7)",
    "quarantine_for_followup.csv": "Rejected rows for follow-up (.csv)",
    "needs_mapping.csv": "Codes that need mapping (.csv)",
    "gap_report.csv": "Gap report (.csv)",
    "conversion_log.csv": "Row-by-row conversion log (.csv)",
}
# (status key, label, what it means for the person using the app)
STATUSES = [
    ("sent", "Sent", "Valid ELR messages, ready for the health department."),
    ("needs_mapping", "Needs mapping", "Uses a local code the maps don't cover yet."),
    ("rejected", "Rejected", "Missing or unreadable essentials. Ask the source to fix and resend."),
    ("duplicate", "Duplicate", "Same result already sent from an earlier row."),
    ("not_reportable", "Not reportable", "Negative or inconclusive. Your state doesn't require these."),
    ("failed_validation", "Failed checks", "Built, but failed validation. Report this as a bug."),
]
USAGE = {"R": "Required", "RE": "Required if known"}
MAX_MESSAGES_SHOWN = 50


def create_app(runs_dir=None):
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024   # 10 MB
    app.config["RUNS_DIR"] = Path(runs_dir or RUNS_DIR)

    # ------------------------------------------------------------------
    # Every form carries a one-time token, so another website can't
    # submit forms to this app behind your back.
    # ------------------------------------------------------------------
    @app.before_request
    def check_form_token():
        if request.method == "POST":
            token = request.form.get("csrf_token", "")
            if not token or token != session.get("csrf_token"):
                abort(400, "This form expired. Reload the page and try again.")

    @app.context_processor
    def page_defaults():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf_token"], "statuses": STATUSES}

    @app.errorhandler(413)
    def too_large(_):
        flash("That file is larger than 10 MB. Split it into smaller exports and convert each one.", "error")
        return redirect(url_for("home"))

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    @app.get("/")
    def home():
        t = convert.load_tables()
        return render_template("home.html", sources=t["sources"].to_dict("records"),
                               runs=list_runs(app.config["RUNS_DIR"]))

    @app.post("/convert")
    def start_conversion():
        t = convert.load_tables()
        sources = t["sources"].set_index("source_id")
        source_id = request.form.get("source_id", "")
        if source_id not in sources.index:
            flash("Pick the source this export came from.", "error")
            return redirect(url_for("home"))

        run_id, run_dir = new_run(app.config["RUNS_DIR"], source_id)
        if request.form.get("input_choice") == "sample":
            sample = config.LEGACY_DIR / sources.loc[source_id, "file"]
            if not sample.exists():
                generate_legacy_data.main()
            input_path = run_dir / f"input{sample.suffix}"
            shutil.copy(sample, input_path)
            original = f"Synthetic sample ({sample.name})"
        else:
            upload = request.files.get("export_file")
            if not upload or not upload.filename:
                shutil.rmtree(run_dir)
                flash("Choose a file to upload, or pick the synthetic sample file.", "error")
                return redirect(url_for("home"))
            original = secure_filename(upload.filename) or "upload"
            suffix = Path(original).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                shutil.rmtree(run_dir)
                flash("Upload a .csv or .txt export.", "error")
                return redirect(url_for("home"))
            input_path = run_dir / f"input{suffix}"
            upload.save(input_path)

        return finish_run(run_id, run_dir, source_id, sources.loc[source_id, "name"], input_path, original)

    @app.post("/runs/<run_id>/rerun")
    def rerun(run_id):
        old_dir = run_path(app.config["RUNS_DIR"], run_id)
        meta = read_meta(old_dir)
        new_id, new_dir = new_run(app.config["RUNS_DIR"], meta["source_id"])
        old_input = next(old_dir.glob("input.*"))
        input_path = new_dir / old_input.name
        shutil.copy(old_input, input_path)
        return finish_run(new_id, new_dir, meta["source_id"], meta["source_name"], input_path,
                          meta["original_filename"], parent=run_id)

    def finish_run(run_id, run_dir, source_id, source_name, input_path, original, parent=None):
        try:
            df, messages, _ = convert.convert([(source_id, input_path)], run_dir / "output", verbose=False)
        except convert.ConversionError as err:
            shutil.rmtree(run_dir)
            flash(str(err), "error")
            return redirect(url_for("home"))
        counts = df["status"].value_counts().to_dict()
        meta = {"run_id": run_id, "created": datetime.now().isoformat(timespec="seconds"),
                "source_id": source_id, "source_name": source_name, "original_filename": original,
                "rows": len(df), "counts": {k: int(v) for k, v in counts.items()}, "parent": parent}
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return redirect(url_for("show_run", run_id=run_id))

    @app.get("/runs/<run_id>")
    def show_run(run_id):
        run_dir = run_path(app.config["RUNS_DIR"], run_id)
        meta = read_meta(run_dir)
        out = run_dir / "output"
        t = convert.load_tables()
        snomed = t["snomed_codes"]
        options = {
            "test code": t["loinc_tests"][["loinc_code", "long_name"]].values.tolist(),
            "result value": snomed.loc[snomed["category"] == "result", ["snomed_code", "display"]].values.tolist(),
            "specimen value": snomed.loc[snomed["category"] == "specimen", ["snomed_code", "display"]].values.tolist(),
        }
        needs = read_csv(out / "needs_mapping.csv")
        needs["mapped_to"] = [current_mapping(t, r) for r in needs.to_dict("records")] if len(needs) else []
        messages = read_messages(out / "elr_messages.hl7")
        gaps = read_csv(out / "gap_report.csv")
        gaps = gaps[gaps["status"] != "OK"].assign(usage_label=lambda g: g["usage"].map(USAGE))
        return render_template(
            "run.html", meta=meta, counts=meta["counts"],
            needs=needs.to_dict("records"), inline_flash=True,
            rejected=read_csv(out / "quarantine_for_followup.csv").to_dict("records"),
            failed=read_csv(out / "conversion_log.csv").query("status == 'failed_validation'").to_dict("records"),
            messages=messages[:MAX_MESSAGES_SHOWN], total_messages=len(messages),
            gaps=gaps.to_dict("records"), options=options, downloads=DOWNLOADS,
            created=pretty_time(meta["created"]))

    @app.get("/runs/<run_id>/messages/<control_id>")
    def show_message(run_id, control_id):
        out = run_path(app.config["RUNS_DIR"], run_id) / "output"
        for msg in read_messages(out / "elr_messages.hl7"):
            if msg["control_id"] == control_id:
                return render_template("message.html", run_id=run_id, msg=msg,
                                       segments=convert.explain_rows(msg["raw"]),
                                       raw=msg["raw"].replace("\r", "\n").strip())
        abort(404)

    @app.get("/runs/<run_id>/download/<name>")
    def download(run_id, name):
        if name not in DOWNLOADS:
            abort(404)
        path = run_path(app.config["RUNS_DIR"], run_id) / "output" / name
        if not path.exists():
            abort(404)
        return send_file(path, as_attachment=True, download_name=f"{run_id}_{name}")

    @app.get("/mappings")
    def mappings():
        t = convert.load_tables()
        loinc = t["loinc_tests"].set_index("loinc_code")["long_name"]
        snomed = t["snomed_codes"].set_index("snomed_code")["display"]
        names = t["sources"].set_index("source_id")["name"]
        tests = t["local_test_map"].assign(source=lambda d: d["source_id"].map(names),
                                           standard=lambda d: d["loinc_code"].map(loinc))
        values = t["value_map"][t["value_map"]["common_field"] != "sex"].assign(
            source=lambda d: d["source_id"].map(names),
            standard=lambda d: d["standard_value"].map(snomed),
            kind=lambda d: d["common_field"].map({"result_local": "Result", "specimen_local": "Specimen"}))
        return render_template("mappings.html", tests=tests.to_dict("records"),
                               values=values.to_dict("records"),
                               loinc=t["loinc_tests"].to_dict("records"),
                               snomed=t["snomed_codes"].to_dict("records"))

    @app.post("/mappings/add")
    def add_mapping():
        run_id = request.form.get("run_id", "")
        back = url_for("show_run", run_id=run_id) if RUN_ID.match(run_id) else url_for("mappings")
        try:
            if request.form.get("kind") == "test code" and request.form.get("confirmed") != "yes":
                raise ValueError("Confirm that the lab that runs this test agreed to the code before saving.")
            message = save_mapping(request.form.get("kind", ""), request.form.get("source_id", ""),
                                   request.form.get("local_value", ""), request.form.get("standard_code", ""))
        except ValueError as err:
            flash(str(err), "error")
            return redirect(back)
        flash(message, "success")
        return redirect(back + "#attention")

    return app


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def new_run(runs_dir, source_id):
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{source_id}-{secrets.token_hex(2)}"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    return run_id, run_dir


def run_path(runs_dir, run_id):
    """Only accept run IDs in our own format, so a URL can never point outside the runs folder."""
    if not RUN_ID.match(run_id) or not (runs_dir / run_id / "meta.json").exists():
        abort(404)
    return runs_dir / run_id


def read_meta(run_dir):
    return json.loads((run_dir / "meta.json").read_text())


def list_runs(runs_dir):
    if not runs_dir.exists():
        return []
    runs = []
    for meta_file in runs_dir.glob("*/meta.json"):
        meta = json.loads(meta_file.read_text())
        meta["pretty_time"] = pretty_time(meta["created"])
        c = meta["counts"]
        meta["attention"] = c.get("needs_mapping", 0) + c.get("rejected", 0) + c.get("failed_validation", 0)
        runs.append(meta)
    return sorted(runs, key=lambda m: m["created"], reverse=True)


def pretty_time(iso):
    return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")


def read_csv(path):
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def read_messages(path):
    if not path.exists():
        return []
    # newline="" keeps HL7's carriage-return segment separators intact
    with open(path, newline="") as f:
        text = f.read()
    if not text.strip():
        return []
    out = []
    for raw in text.split("\n"):
        p = parse(raw)
        out.append({"raw": raw, "control_id": get(p, "MSH-10"),
                    "patient": f"{get(p, 'PID-5.1')}, {get(p, 'PID-5.2')}",
                    "test": get(p, "OBX-3.2"), "result": get(p, "OBX-5.2"),
                    "collected": format_hl7_date(get(p, "OBR-7"))})
    return out


def format_hl7_date(value):
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) >= 8 else value


def current_mapping(t, need):
    """The standard meaning a local value has now, if it was mapped after this run."""
    local = str(need["local_value"]).strip().lower()
    if need["kind"] == "test code":
        m = t["local_test_map"]
        hit = m[(m["source_id"] == need["source_id"]) & (m["local_test"] == local)]
        if len(hit):
            return t["loinc_tests"].set_index("loinc_code").loc[hit.iloc[0]["loinc_code"], "long_name"]
    else:
        field = "result_local" if need["kind"] == "result value" else "specimen_local"
        m = t["value_map"]
        hit = m[(m["source_id"] == need["source_id"]) & (m["common_field"] == field) & (m["local_value"] == local)]
        if len(hit):
            return t["snomed_codes"].set_index("snomed_code").loc[hit.iloc[0]["standard_value"], "display"]
    return ""


def save_mapping(kind, source_id, local_value, standard_code):
    """Add or replace one mapping. Only codes in the verified tables are accepted."""
    t = convert.load_tables()
    if source_id not in set(t["sources"]["source_id"]):
        raise ValueError("Unknown source.")
    local = local_value.strip().lower()
    if not local:
        raise ValueError("The local value is empty.")
    snomed = t["snomed_codes"]
    if kind == "test code":
        if standard_code not in set(t["loinc_tests"]["loinc_code"]):
            raise ValueError("Pick a LOINC code from the verified list.")
        table, key_cols = t["local_test_map"], ["source_id", "local_test"]
        row = {"source_id": source_id, "local_test": local, "loinc_code": standard_code}
        name, label = "local_test_map.csv", t["loinc_tests"].set_index("loinc_code").loc[standard_code, "long_name"]
    elif kind in ("result value", "specimen value"):
        category = "result" if kind == "result value" else "specimen"
        allowed = set(snomed.loc[snomed["category"] == category, "snomed_code"])
        if standard_code not in allowed:
            raise ValueError("Pick a SNOMED CT code from the verified list.")
        field = "result_local" if category == "result" else "specimen_local"
        table, key_cols = t["value_map"], ["source_id", "common_field", "local_value"]
        row = {"source_id": source_id, "common_field": field, "local_value": local,
               "standard_value": standard_code}
        name, label = "value_map.csv", snomed.set_index("snomed_code").loc[standard_code, "display"]
    else:
        raise ValueError("Unknown mapping type.")

    keep = ~(table[key_cols] == pd.Series({k: row[k] for k in key_cols})).all(axis=1)
    updated = pd.concat([table[keep], pd.DataFrame([row])], ignore_index=True)
    updated.to_csv(config.MAPPINGS_DIR / name, index=False)
    source_name = t["sources"].set_index("source_id").loc[source_id, "name"]
    return (f"Saved. At {source_name}, \"{local_value.strip()}\" now means {label}. "
            "Convert again to apply it.")


if __name__ == "__main__":
    print("Lab Reporting Desk is running. Open http://127.0.0.1:5000 in your browser.")
    print("Prototype with synthetic data only. Press Ctrl+C to stop.")
    create_app().run(host="127.0.0.1", port=5000, debug=False)
