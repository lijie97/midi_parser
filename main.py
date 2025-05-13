#!/usr/bin/env python3
"""
jianpu2midi.py
将自定义 ASCII 简谱 (单声部首调制) 转成 MIDI，并尝试播放。

用法:
    python jianpu2midi.py <score.txt> [output.mid]

示例:
    python jianpu2midi.py twinkle.txt twinkle.mid


# file        = { header_line | comment_line }, { measure } ;
# header_line = "#" , key , "=", value , NEWLINE ;
# comment_line= ";" , { ANY } , NEWLINE ;
# measure     = element , { SP , element } , ( "|" | "||" ) , NEWLINE ;
# element     = note | rest ;
# note        = degree , [ accidental ] , [ octave ] , [ duration ] ,
#               [ dots ] , [ tie ] ;
# degree      = "1"…"7" ;
# accidental  = "#" | "b" ;
# octave      = { "'" | "," } ;
# duration    = "w" | "h" | "q" | "e" | "s" | "t" | "/" , unsigned_int ;
# dots        = "." | ".." ;
# tie         = "-" ;
# rest        = "R" | "0" ;
"""

import re
import sys
import time
from pathlib import Path

from mido import MidiFile, MidiTrack, Message, MetaMessage, bpm2tempo

# ───────────── 常量 ─────────────────────────────────────────
TICKS_PER_BEAT = 480
MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11]          # 大调度数 → 半音
DUR2BEAT = {'w': 4, 'h': 2, 'q': 1, 'e': 0.5, 's': 0.25, 't': 0.125}
NOTE2MIDI = {
    'C': 60, 'C#': 61, 'Db': 61, 'D': 62, 'D#': 63, 'Eb': 63,
    'E': 64, 'F': 65, 'F#': 66, 'Gb': 66, 'G': 67, 'G#': 68, 'Ab': 68,
    'A': 69, 'A#': 70, 'Bb': 70, 'B': 71,
}
METRO_CH, METRO_NOTE = 9, 37                      # 通道10、Side Stick

# ───────────── 正则 ────────────────────────────────────────
_NOTE_RE = re.compile(
    r"(?P<deg>[1-7])(?P<acc>[#b]?)(?P<oct>[',]*)(?P<dur>[whqest]|/\d+)?"
    r"(?P<dots>\.*)(?P<tie>-?)$"
)
_REST_RE = re.compile(r"(?P<r>[R0])(?P<dur>[whqest]|/\d+)?(?P<dots>\.*)$")

# ───────────── 工具函数 ────────────────────────────────────
def _beats(dur: str, dots: str, unit: float) -> float:
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


# ───────────── 解析器 ──────────────────────────────────────
class ScoreParser:
    def __init__(self, text: str):
        self.meta: dict[str, str] = {}
        self.tokens: list[str] = []
        self._parse(text)

    def _parse(self, text: str):
        body = []
        for line in (l.rstrip() for l in text.splitlines() if l.strip()):
            if line.startswith(';'):
                continue
            if line.startswith('#'):
                k, v = line[1:].split('=', 1)
                self.meta[k.strip().upper()] = v.strip()
            else:
                body.append(line)

        for ln in body:
            for tok in ln.split():
                if tok not in ('|', '||'):
                    self.tokens.append(tok)


# ───────────── MIDI 生成 ───────────────────────────────────
def build_midi(parser: ScoreParser,
               outfile: Path,
               metro_on: bool = False,
               metro_vel: tuple[int, int] = (90, 60)) -> Path:

    unit = DUR2BEAT.get(parser.meta.get('UNIT', 'q').lower(), 1)
    tempo_bpm = int(parser.meta.get('TEMPO', 120))
    key_root = _norm_key(parser.meta.get('KEY', 'C'))
    if key_root not in NOTE2MIDI:
        raise ValueError(f'#KEY 不支持: {key_root}')

    num, den = (int(x) for x in parser.meta.get('TIME', '4/4').split('/'))
    if den & (den - 1):
        raise ValueError(f'#TIME 分母必须是 2 的幂：{den}')

    mid = MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    melody = MidiTrack(); mid.tracks.append(melody)
    melody.extend([
        MetaMessage('set_tempo', tempo=bpm2tempo(tempo_bpm)),
        MetaMessage('time_signature', numerator=num, denominator=den,
                    clocks_per_click=24, notated_32nd_notes_per_beat=8)
    ])

    delta = cur_tick = 0
    toks = parser.tokens
    i, n = 0, len(toks)

    while i < n:
        tok = toks[i]

        # 休止
        rst = _REST_RE.match(tok)
        if rst:
            delta += int(_beats(rst.group('dur') or '', rst.group('dots'), unit) * TICKS_PER_BEAT)
            i += 1
            continue

        m = _NOTE_RE.match(tok)
        if not m:
            raise ValueError(f'无法解析: {tok}')

        deg = int(m.group('deg')); acc = m.group('acc')
        oct_shift = m.group('oct').count("'") - m.group('oct').count(',')
        beats = _beats(m.group('dur') or '', m.group('dots'), unit)
        pitch = _degree2midi(deg, acc, oct_shift, key_root)

        # 连音线合并 —— 比较绝对 pitch
        j = i
        tie = bool(m.group('tie'))
        while tie and j + 1 < n:
            nxt_tok = toks[j + 1]
            nxt_note = _NOTE_RE.match(nxt_tok)
            if not nxt_note:
                break
            n_deg = int(nxt_note.group('deg'))
            n_acc = nxt_note.group('acc')
            n_oct = nxt_note.group('oct').count("'") - nxt_note.group('oct').count(',')
            if _degree2midi(n_deg, n_acc, n_oct, key_root) != pitch:
                break
            beats += _beats(nxt_note.group('dur') or '', nxt_note.group('dots'), unit)
            tie = bool(nxt_note.group('tie'))
            j += 1

        length = int(beats * TICKS_PER_BEAT)
        melody.append(Message('note_on', note=pitch, velocity=64, time=delta))
        melody.append(Message('note_off', note=pitch, velocity=64, time=length))
        cur_tick += delta + length        # ★ 修正：包含休止
        delta = 0
        i = j + 1

    melody.append(MetaMessage('end_of_track', time=0))

    # ── 节拍器轨 ──
    if metro_on:
        click = MidiTrack(); mid.tracks.append(click)
        beat_ticks = int(TICKS_PER_BEAT * 4 / den)
        click_len = int(beat_ticks * 0.2)
        t = beat_idx = 0
        while t < cur_tick:
            dt = 0 if t == 0 else beat_ticks - click_len
            vel = metro_vel[0] if beat_idx % num == 0 else metro_vel[1]
            click.append(Message('note_on', channel=METRO_CH,
                                 note=METRO_NOTE, velocity=vel, time=dt))
            click.append(Message('note_off', channel=METRO_CH,
                                 note=METRO_NOTE, velocity=0, time=click_len))
            t += beat_ticks; beat_idx += 1
        click.append(MetaMessage('end_of_track', time=0))

    mid.save(outfile)
    return outfile


# ───────────── 实时播放 (可选) ───────────────────────────────
def play_midi(path: Path):
    try:
        import mido, mido.backends.rtmidi  # noqa: F401
        port = mido.open_output()
        for msg in MidiFile(path):
            time.sleep(msg.time)
            if not msg.is_meta:
                port.send(msg)
    except Exception as e:
        print(f"（提示）无法实时播放，已生成 MIDI：{path.name}\n原因：{e}")


# ───────────── CLI ─────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)

    txt_path = Path(sys.argv[1])
    out_path = (Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith('-')
                else txt_path.with_suffix('.mid'))

    metro_on, metro_vel = False, (90, 60)
    for arg in sys.argv[2:]:
        if arg in ('-m', '--metronome', '--metro'):
            metro_on = True
        elif arg.startswith('--metronome=') or arg.startswith('--metro='):
            metro_on = True
            try:
                acc, reg = (int(x) for x in arg.split('=', 1)[1].split(','))
                metro_vel = (max(0, min(acc, 127)), max(0, min(reg, 127)))
            except ValueError:
                print('节拍器力度格式应为 “A,R” (0–127)，已使用默认 90,60')

    parser = ScoreParser(txt_path.read_text(encoding='utf-8'))
    mid = build_midi(parser, out_path, metro_on, metro_vel)
    print(f"✓ 已生成 MIDI: {mid}")
    play_midi(mid)


if __name__ == "__main__":
    main()
