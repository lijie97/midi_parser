import re
# ──────────── 正则 ──────────────────────────────────────
_NOTE_RE = re.compile(
    r"(?P<deg>[1-7])(?P<acc>[#b]?)(?P<oct>[',]*)(?P<dur>[whqest]|/\d+)?"
    r"(?P<dots>\.*)(?P<tie>\^?)$"
)
_REST_RE = re.compile(r"(?P<r>[R0])(?P<dur>[whqest]|/\d+)?(?P<dots>\.*)$")
_CHORD_RE = re.compile(r"^[A-Ga-g][#b]?[^/]*$|^O$")    # "不像音符/休止" 即视作和弦
