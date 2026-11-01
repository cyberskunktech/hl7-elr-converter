# Legacy Lab Data to ELR (prototype)

**All data in this project is synthetic. No real people, facilities, or identifiers.**

Converts lab result exports from older clinical systems into HL7 version 2.5.1
Electronic Laboratory Reporting (ELR) messages, the standard format for sending
reportable lab results to a public health department.

## The problem it solves

Different departments, clinics, and labs export results in different shapes:
their own column names, their own internal test codes, their own date formats,
and their own gaps. Public health needs one standard format with standard codes.

## What it does

1. **Extract** each source file exactly as received.
2. **Harmonize** each source into one common layout using that source's field map.
3. **Code** local values into standards: tests to LOINC, results and specimens to
   SNOMED CT, sex to HL7 codes. Ordering facilities are looked up in a directory.
4. **Classify** every row: rejected, needs mapping, duplicate, not reportable, or ready.
5. **Build** an HL7 ORU^R01 message for each ready row.
6. **Validate** every message by reading it back and checking it against our rules.
7. **Report** the messages, a row-by-row log, follow-up lists, and a gap report per source.

## Run it

Install the requirements once from the repository root (see the main README), then:

```
cd legacy_to_elr
python generate_legacy_data.py        # synthetic legacy files + answer key
python convert.py                     # convert and report
python -m unittest discover tests -v  # check results against the answer key
```

Needs Python 3.11+ and pandas 3.0 (tested on Python 3.12 and pandas 3.0.2). On Windows, the `tzdata` package supplies time zone data.

## Use the Lab Reporting Desk (web interface)

```
cd legacy_to_elr
python app.py
```

Then open http://127.0.0.1:5000 in your browser. Pick a source, convert its
synthetic sample file (or upload an export), and you'll see:

- How every row turned out, at a glance
- **Needs your attention:** codes to map, with a form to map each one to a
  verified standard code, and rows to send back to the source
- **Ready to send:** every message, with a field-by-field explanation of each one
- **What this source can't supply yet:** the gap report
- Downloads for the messages and every report

After mapping a code, choose "Convert again with current mappings" to see those
rows go through. Each conversion is kept under `runs/` so you can return to it.

The app runs only on your computer (127.0.0.1) and loads nothing from the
internet. Press Ctrl+C in the terminal to stop it. It is a prototype: use
synthetic data only.

## Where things live

| Path | What it is |
|---|---|
| `mappings/` | Everything a non-programmer edits: field maps, value maps, test code map, verified code tables, facility directory, ELR requirements |
| `convert.py` | The seven steps above |
| `app.py`, `templates/`, `static/` | The Lab Reporting Desk web interface |
| `hl7_builder.py` | Builds and reads HL7 v2 text, including escaping special characters |
| `output/` | Messages, conversion log, quarantine list, needs-mapping list, gap report, an annotated sample message |

## What is verified, and what is not yet

Every LOINC and SNOMED CT code in `mappings/` was checked against a published
source, which is recorded next to it. The MSH-21 profile identifier and the CLIA
identifier OID were checked against published guides (see `config.py`).

Not done yet, and needed before real use:
- Testing with the NIST ELR validator and the state's onboarding process.
- The state's own ELR guide, which adds rules on top of the national one.
  Requirement levels in `mappings/elr_requirements.csv` are prototype assumptions.
- Race and ethnicity coding (no source collects them yet; see the gap report).
- Corrected results, acknowledgments, batching, and secure transport.
- Each local-to-LOINC mapping must be confirmed by the lab that runs the test.
