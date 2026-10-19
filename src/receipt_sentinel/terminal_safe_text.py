"""terminal_safe_text

Untrusted text (email headers, filenames, model output) made safe to print: control and
invisible characters stripped (spec §4.5), and one value kept to one line so it can't
forge a row of a report.

Status:
    None
Issues:
    None
Todo:
    None
"""

import re

# C0/C1 controls (minus newline), DEL, and invisible/bidi marks that can disguise text
_UNSAFE_TERMINAL_CHARS = re.compile(
    r"[\x00-\x09\x0b-\x1f\x7f-\x9f؜‎‏  ‪-‮"
    r"⁦-⁩﻿]"
)


def render_terminal_safe(text: str) -> str:
    """Strip control characters so sender-chosen text can't drive the terminal."""
    return _UNSAFE_TERMINAL_CHARS.sub("", text)


def render_one_line(text: object) -> str:
    """One untrusted value on one line: a newline in a subject, filename or model output
    must not be able to print what looks like a report row."""
    return render_terminal_safe(" ".join(str(text).split()))
