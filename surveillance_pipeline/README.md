# Disease Surveillance Pipeline (prototype)

**All data in this project is synthetic. No real people, places, or facilities.**

A small pipeline that takes raw disease case reports, the kind a health
department receives from clinics, labs, and hospitals, and turns them into
trustworthy weekly case counts.

## What it does

1. **Extract** the raw report file exactly as received.
2. **Standardize** formats so the same thing always looks the same
   ("F", "female", "Female" all become "Female").
3. **Validate** each report. Unusable reports go to a follow-up list with a
   reason. Usable reports with one bad field get that field blanked and flagged.
4. **Deduplicate** reports into cases: one person with one illness counts once,
   even if three facilities reported them.
5. **Enrich** each case with its epi week (CDC MMWR system), age group, and
   reporting delay.
6. **Load** everything into a SQLite database with a log of each run.
7. **Summarize**: weekly counts, epi curve chart, district table (with a public
   version that hides small counts), and a data quality report.

## Run it

Install the requirements once from the repository root (see the main README), then:

```
cd surveillance_pipeline
python generate_data.py              # make synthetic reports + answer key
python pipeline.py                   # run the pipeline
python -m unittest discover tests -v # check the results against the answer key
```

## Files

| File | What it is |
|---|---|
| `config.py` | Every setting and reference list in one place |
| `generate_data.py` | Creates synthetic reports with realistic mistakes, plus an answer key |
| `pipeline.py` | The seven steps above |
| `tests/test_pipeline.py` | Checks epi weeks, cleaning rules, and end-to-end accuracy |
| `data/surveillance.db` | Database: raw_reports, rejected_reports, cases, case_reports, pipeline_runs |
| `output/` | Weekly counts, district tables, follow-up list, chart, quality report |

## How we know it works

Because the data is synthetic, the generator records the truth behind every
report. The tests confirm the pipeline rejects exactly the unusable reports,
never merges two different illnesses, never splits one illness into two cases,
and makes the planted measles outbreak visible.
