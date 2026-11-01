"""
Settings for the legacy-to-ELR converter.

ALL DATA IN THIS PROJECT IS SYNTHETIC. People, facilities, addresses, phone
numbers, CLIA numbers and OIDs are invented. Our own OIDs sit under
2.16.840.1.113883.19, the branch HL7 reserves for examples, so they can never
collide with a real organization's identifiers.

Anything marked PLACEHOLDER must be replaced with the organization's real
values, usually taken from the state health department's ELR guide.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent
LEGACY_DIR = PROJECT_DIR / "data" / "legacy"
ANSWER_KEY_FILE = LEGACY_DIR / "answer_key.csv"   # exists only because the data is synthetic
MAPPINGS_DIR = PROJECT_DIR / "mappings"
OUTPUT_DIR = PROJECT_DIR / "output"

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------
RANDOM_SEED = 7
ROWS_PER_SOURCE = 90
COLLECTION_START = "2026-08-02"
COLLECTION_END = "2026-09-26"

# ---------------------------------------------------------------------------
# Location (PLACEHOLDERS)
# ---------------------------------------------------------------------------
TIMEZONE = "America/New_York"   # PLACEHOLDER: the organization's time zone
STATE = "XX"                    # PLACEHOLDER: the organization's two-letter state code

# ---------------------------------------------------------------------------
# Message header (MSH) values
# ---------------------------------------------------------------------------
# National ELR 2.5.1 Release 1 profile identifier (MSH-21). Verified against
# New Mexico's ELR onboarding guide (nmhealth.org/publication/view/guide/9052).
# Some states use the "Ack" variant instead; check your state's guide.
MESSAGE_PROFILE = ("PHLabReport-NoAck", "", "2.16.840.1.114222.4.10.3", "ISO")

SENDING_APPLICATION = ("LEGACY2ELR", "2.16.840.1.113883.19.4.1", "ISO")
# PLACEHOLDERS: every state publishes its own literal values for these two.
RECEIVING_APPLICATION = ("SYNTH_DOH_ELR", "2.16.840.1.113883.19.4.2", "ISO")
RECEIVING_FACILITY = ("SYNTH_DOH", "2.16.840.1.113883.19.4.3", "ISO")
PROCESSING_ID = "P"   # P = production. States say what to use during testing.

# The CLIA program's identifier, used to label CLIA numbers.
# Verified: HL7 US Lab performing-lab profile, fixed value urn:oid:2.16.840.1.113883.4.7
CLIA_OID = "2.16.840.1.113883.4.7"

SOFTWARE_NAME = "Legacy-to-ELR Prototype (synthetic data)"
SOFTWARE_VERSION = "0.1"

# ---------------------------------------------------------------------------
# What gets sent
# ---------------------------------------------------------------------------
# Only these result codes are reported (SNOMED CT: Positive, Detected).
# State rules decide what is reportable; some states also want negatives
# for certain tests, such as hepatitis panels. Change this set to match.
REPORTABLE_RESULT_CODES = {"10828004", "260373001"}

# The same result exported twice is sent once.
DUPLICATE_KEY = ["source_id", "record_id", "loinc_code"]
