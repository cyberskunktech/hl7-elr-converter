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
"""

SEGMENT_END = "\r"
ENCODING_CHARACTERS = "^~\\&"

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
    """Read a value like 'PID-5.1' (segment-field.component) from a parsed message."""
    seg_name, rest = location.split("-")
    field_no, _, comp_no = rest.partition(".")
    for name, fields in parsed:
        if name == seg_name:
            value = fields[int(field_no)] if int(field_no) < len(fields) else ""
            if comp_no:
                comps = value.split("^")
                value = comps[int(comp_no) - 1] if int(comp_no) <= len(comps) else ""
            return unescape(value)
    return ""
