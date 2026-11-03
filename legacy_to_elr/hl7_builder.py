"""
Build and read HL7 version 2 messages.

How HL7 v2 is laid out, in plain language:
  - A message is a stack of SEGMENTS, one per line, each starting with a
    three-letter name (MSH = header, PID = patient, OBX = result, ...).
  - Inside a segment, FIELDS are separated by |
  - Inside a field, COMPONENTS are separated by ^   (last^first)
  - Inside a component, SUBCOMPONENTS are separated by &
  - Segments end with a carriage return, not a normal newline.

Because those characters have special meaning, real text that contains them
(a clinic named "Health & Wellness") must be ESCAPED, or the receiver will
split it in the wrong place. That is what esc() does.

ELR also declares a fifth special character, # (the "truncation character",
borrowed from HL7 version 2.7). A # at the very END of a value tells the
receiver the value was cut short. Sources disagree on how to escape it, so
the converter doesn't guess: its validator flags any value ending in #.
"""

SEGMENT_END = "\r"
# ELR requires all five, including the truncation character (ONC rule ELR-013)
ENCODING_CHARACTERS = "^~\\&#"

_ESCAPES = [("\\", "\\E\\"), ("|", "\\F\\"), ("^", "\\S\\"), ("&", "\\T\\"), ("~", "\\R\\")]


def esc(value):
    """Escape special characters in plain text. Backslash must go first."""
    text = "" if value is None else str(value)
    for char, code in _ESCAPES:
        text = text.replace(char, code)
    return text


class Raw(str):
    """Text that is already built (for example subcomponents) and must not be escaped again."""


def _part(value):
    if value is None or (isinstance(value, float) and value != value):  # None or NaN
        return ""
    return value if isinstance(value, Raw) else esc(value)


def sub(*parts):
    """Join subcomponents with &."""
    return Raw("&".join(_part(p) for p in parts).rstrip("&"))


def fld(*components):
    """Join components with ^, dropping empty trailing components."""
    return Raw("^".join(_part(c) for c in components).rstrip("^"))


def segment(name, fields):
    """fields is a dict {field_number: value}. Missing numbers become empty fields."""
    if name == "MSH":
        # MSH-1 is the field separator itself and MSH-2 holds the encoding characters
        highest = max(fields)
        body = [_part(fields.get(i)) for i in range(3, highest + 1)]
        return "MSH|" + ENCODING_CHARACTERS + "|" + "|".join(body)
    highest = max(fields)
    body = [_part(fields.get(i)) for i in range(1, highest + 1)]
    return name + "|" + "|".join(body)


def message(segments):
    return SEGMENT_END.join(segments) + SEGMENT_END


# ---------------------------------------------------------------------------
# Reading (used by the validator and the tests)
# ---------------------------------------------------------------------------
def parse(msg):
    """Return a list of (segment_name, fields) where fields[n] is field n."""
    parsed = []
    for line in filter(None, msg.split(SEGMENT_END)):
        parts = line.split("|")
        name = parts[0]
        if name == "MSH":
            fields = [None, "|", parts[1]] + parts[2:]   # line up so fields[n] is MSH-n
        else:
            fields = [None] + parts[1:]
        parsed.append((name, fields))
    return parsed


def unescape(text):
    for char, code in reversed(_ESCAPES):
        text = text.replace(code, char)
    return text


def get(parsed, location):
    """Read a value like 'PID-5.1' or 'SPM-2.2.4' (segment-field.component.subcomponent)."""
    seg_name, rest = location.split("-")
    numbers = [int(n) for n in rest.split(".")]
    for name, fields in parsed:
        if name == seg_name:
            value = fields[numbers[0]] if numbers[0] < len(fields) else ""
            for depth, n in enumerate(numbers[1:]):
                parts = value.split("^" if depth == 0 else "&")
                value = parts[n - 1] if n <= len(parts) else ""
            return unescape(value)
    return ""
