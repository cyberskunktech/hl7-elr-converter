"""
Project settings, all in one place.

Anything you might want to change (which diseases, which districts, how many
fake records, what counts as a duplicate) lives here, so you never have to dig
through the processing code to adjust the pipeline.

ALL DATA IN THIS PROJECT IS SYNTHETIC. No real people, places, or facilities.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Folders and files
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent
RAW_DIR = PROJECT_DIR / "data" / "raw"
RAW_FILE = RAW_DIR / "case_reports_raw.csv"
# The answer key only exists because the data is synthetic. Real data never
# comes with one. We use it to prove the pipeline gets the right answer.
ANSWER_KEY_FILE = RAW_DIR / "answer_key.csv"
DB_PATH = PROJECT_DIR / "data" / "surveillance.db"
OUTPUT_DIR = PROJECT_DIR / "output"

# ---------------------------------------------------------------------------
# Synthetic data settings
# ---------------------------------------------------------------------------
RANDOM_SEED = 2026            # same seed = same fake data on every run
N_BACKGROUND_CASES = 520      # everyday cases spread across the period
PERIOD_START = "2026-01-04"   # first day of MMWR week 1, 2026 (a Sunday)
PERIOD_END = "2026-06-27"     # last day of MMWR week 25, 2026 (a Saturday)
EXTRACT_DATE = "2026-07-01"   # the day we "pulled" data from the reporting system
DUPLICATE_RATE = 0.10         # share of cases reported more than once
N_REINFECTIONS = 3            # same person, same disease, months apart (two real cases)

# A planted outbreak, so we can confirm the pipeline makes it visible.
OUTBREAK = {
    "disease": "Measles",
    "district": "Riverside",
    "start": "2026-03-15",
    "end": "2026-04-11",
    "n_cases": 35,
}

# ---------------------------------------------------------------------------
# Reference lists (the "official" spellings everything gets converted to)
# ---------------------------------------------------------------------------
# Relative frequency of each disease among background cases.
DISEASES = {
    "Salmonellosis": 0.36,
    "Campylobacteriosis": 0.31,
    "Pertussis": 0.16,
    "Hepatitis A": 0.14,
    "Measles": 0.03,
}

# Every spelling we have seen for each disease, lowercased.
DISEASE_ALIASES = {
    "salmonellosis": "Salmonellosis",
    "salmonella": "Salmonellosis",
    "salmonella infection": "Salmonellosis",
    "campylobacteriosis": "Campylobacteriosis",
    "campylobacter": "Campylobacteriosis",
    "campy": "Campylobacteriosis",
    "pertussis": "Pertussis",
    "whooping cough": "Pertussis",
    "hepatitis a": "Hepatitis A",
    "hep a": "Hepatitis A",
    "hav": "Hepatitis A",
    "measles": "Measles",
    "rubeola": "Measles",
}

DISTRICTS = {  # name: share of population
    "Northgate": 0.25,
    "Riverside": 0.20,
    "Eastfield": 0.22,
    "Westbrook": 0.18,
    "Southvale": 0.15,
}

FACILITIES = [
    "Central Hospital Lab",
    "Regional Reference Lab",
    "Riverside Clinic",
    "Northgate Health Center",
    "Eastfield Urgent Care",
]

# Case classification, strongest first. A later lab result can upgrade a case.
CASE_STATUS_RANK = {"Confirmed": 3, "Probable": 2, "Suspect": 1}
CASE_STATUS_ALIASES = {
    "confirmed": "Confirmed",
    "conf": "Confirmed",
    "probable": "Probable",
    "prob": "Probable",
    "suspect": "Suspect",
    "suspected": "Suspect",
}

SEX_ALIASES = {"f": "Female", "female": "Female", "m": "Male", "male": "Male"}
YES_NO_ALIASES = {"y": "Yes", "yes": "Yes", "1": "Yes", "n": "No", "no": "No", "0": "No"}

# ---------------------------------------------------------------------------
# Pipeline rules
# ---------------------------------------------------------------------------
# The only date formats we accept. We never let software guess, because
# 03/04/2026 means March 4 in the US and 3 April in most other countries.
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y"]

# Reports for the same person and disease within this many days of each other
# are treated as one case. Further apart = a new illness.
DEDUP_WINDOW_DAYS = 90

AGE_MIN, AGE_MAX = 0, 110

AGE_BINS = [0, 5, 18, 50, 65, 200]
AGE_LABELS = ["0-4", "5-17", "18-49", "50-64", "65+"]

# Weeks ending within this many days of the extract date are marked as
# "likely incomplete", because their reports have not all arrived yet.
INCOMPLETE_WINDOW_DAYS = 14

# In tables meant for the public, counts from 1 to 4 are shown as "<5"
# so that nobody can work out who a small number of cases might be.
SMALL_CELL_THRESHOLD = 5
