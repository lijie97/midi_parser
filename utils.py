from constants import DUR2BEAT, MAJOR_INTERVALS, NOTE2MIDI
# ──────────── 工具 ──────────────────────────────────────
def _beats(dur: str, dots: str, unit: float) -> float:
    """时值字符串 → 四分音拍"""
    b = unit if not dur else (unit * float(dur) if dur.startswith('/')
                              else DUR2BEAT[dur] * unit)
    if dots:
        b *= 1.5 if dots == '.' else 1.75
    return b


def _norm_key(name: str) -> str:
    name = name.strip()
    if len(name) == 1:
        return name.upper()
    if len(name) == 2 and name[1] in '#b':
        return name[0].upper() + name[1]
    raise ValueError(f'#KEY 无法识别: {name}')


def _degree2midi(deg: int, acc: str, oct_shift: int, key_root: str) -> int:
    root = NOTE2MIDI[key_root]
    semitone = MAJOR_INTERVALS[deg - 1] + (1 if acc == '#' else -1 if acc == 'b' else 0)
    return root + semitone + 12 * oct_shift
