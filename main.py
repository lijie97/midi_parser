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

# ──────────── 常量 ──────────────────────────────────────
TICKS_PER_BEAT = 480
MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11]     # 大调度数 → 半音
DUR2BEAT = {'w': 4, 'h': 2, 'q': 1, 'e': .5, 's': .25, 't': .125}
NOTE2MIDI = {                                 # C4 = 60
    'C': 60, 'C#': 61, 'Db': 61, 'D': 62, 'D#': 63, 'Eb': 63,
    'E': 64, 'F': 65, 'F#': 66, 'Gb': 66, 'G': 67, 'G#': 68, 'Ab': 68,
    'A': 69, 'A#': 70, 'Bb': 70, 'B': 71,
}
METRO_CH, METRO_NOTE = 9, 37                 # 节拍器（通道10）
CHORD_CH = 1                                 # 和弦（通道2）
CHORD_VELOCITY = 50

# ──────────── 正则 ──────────────────────────────────────
_NOTE_RE = re.compile(
    r"(?P<deg>[1-7])(?P<acc>[#b]?)(?P<oct>[',]*)(?P<dur>[whqest]|/\d+)?"
    r"(?P<dots>\.*)(?P<tie>-?)$"
)
_REST_RE = re.compile(r"(?P<r>[R0])(?P<dur>[whqest]|/\d+)?(?P<dots>\.*)$")
_CHORD_RE = re.compile(r"^[A-Ga-g][#b]?[^/]*$|^O$")    # "不像音符/休止" 即视作和弦

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


def parse_chord(sym: str) -> list[int]:
    """
    解析和弦符号，返回MIDI音符列表:
    - 大三和弦: 'C', 'F#', 'Bb'
    - 小三和弦: 'Cm', 'F#m', 'Bbm'
    - 增三和弦: 'Caug', 'F#+', 'Bbaug'
    - 减三和弦: 'Cdim', 'F#o', 'Bbo'
    - 挂四和弦: 'Csus4', 'F#sus4'
    - 挂二和弦: 'Csus2', 'F#sus2'
    - 属七和弦: 'C7', 'F#7', 'Bb7'
    - 大七和弦: 'CM7', 'Cmaj7', 'F#M7', 'BbM7'
    - 小七和弦: 'Cm7', 'F#m7', 'Bbm7'
    - 半减七和弦: 'Cm7b5', 'F#m7b5'
    - 减七和弦: 'Cdim7', 'Co7', 'F#o7'
    - 无和弦: 'O' → []
    """
    if sym.upper() == 'O':
        return []
    
    sym = sym.strip()
    root = sym[0].upper()
    idx = 1
    
    # 处理升降号
    if len(sym) > 1 and sym[1] in '#b':
        root += sym[1]
        idx += 1
    
    # 提取和弦类型
    qual = sym[idx:].lower()
    
    if root not in NOTE2MIDI:
        raise ValueError(f'未知和弦根音: {root}')

    root_pitch = NOTE2MIDI[root] - 12  # 低1八度
    
    # 默认大三和弦
    intervals = (0, 4, 7)
    
    # 解析各种和弦类型
    if qual == 'm':  # 小三和弦
        intervals = (0, 3, 7)
    elif qual in ('aug', '+'):  # 增三和弦 
        intervals = (0, 4, 8)
    elif qual in ('dim', 'o', '°'):  # 减三和弦
        intervals = (0, 3, 6)
    elif qual == 'sus4':  # 挂四和弦
        intervals = (0, 5, 7)
    elif qual == 'sus2':  # 挂二和弦
        intervals = (0, 2, 7)
    elif qual == '7':  # 属七和弦
        intervals = (0, 4, 7, 10)
    elif qual in ('m7', 'min7'):  # 小七和弦
        intervals = (0, 3, 7, 10)
    elif qual in ('maj7', 'M7'):  # 大七和弦
        intervals = (0, 4, 7, 11)
    elif qual in ('m7b5', 'ø'):  # 半减七和弦
        intervals = (0, 3, 6, 10)
    elif qual in ('dim7', 'o7', '°7'):  # 减七和弦
        intervals = (0, 3, 6, 9)
    elif qual == '6':  # 大六和弦
        intervals = (0, 4, 7, 9)
    elif qual == 'm6':  # 小六和弦
        intervals = (0, 3, 7, 9)
    elif qual in ('9', '7(9)'):  # 属九和弦(简化版,只取前4个音)
        intervals = (0, 4, 7, 10, 14)
    
    return [root_pitch + iv for iv in intervals]

# ──────────── 解析 ──────────────────────────────────────
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
                self.tokens.append(tok)        # ★ 保留 | 和 ||

# ──────────── MIDI 生成 ─────────────────────────────────
def build_midi(parser: ScoreParser,
               outfile: Path,
               metro_on: bool = False,
               metro_vel: tuple[int, int] = (90, 60)) -> Path:

    unit = DUR2BEAT.get(parser.meta.get('UNIT', 'q').lower(), 1)
    tempo_bpm = int(parser.meta.get('TEMPO', 120))
    key_root = _norm_key(parser.meta.get('KEY', 'C'))
    if key_root not in NOTE2MIDI:
        raise ValueError(f'#KEY 不支持: {key_root}')

    ts_num, ts_den = (int(x) for x in parser.meta.get('TIME', '4/4').split('/'))
    if ts_den & (ts_den - 1):
        raise ValueError('#TIME 分母必须是 2 的幂')
    beats_per_measure = ts_num * 4 / ts_den   # 四分音拍为 1

    mid = MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    melody = MidiTrack(); mid.tracks.append(melody)
    chords = MidiTrack(); mid.tracks.append(chords)
    chords.append(Message('program_change', channel=CHORD_CH, program=48))  # 弦乐

    # Meta
    melody.append(MetaMessage('set_tempo', tempo=bpm2tempo(tempo_bpm)))
    melody.append(MetaMessage('time_signature',
                              numerator=ts_num, denominator=ts_den,
                              clocks_per_click=24, notated_32nd_notes_per_beat=8))
    melody.append(MetaMessage('track_name', name='Melody', time=0))
    chords.append(MetaMessage('track_name', name='Chords', time=0))

    cur_tick = 0            # 绝对时间
    delta_melody = 0        # 距离下一 melody 事件
    measure_beats = 0       # 当前小节已累积拍数

    chord_active = []
    chord_last_tick = 0

    toks = parser.tokens
    i, n = 0, len(toks)
    while i < n:
        tok = toks[i]

        # ── 小节线 ──
        if tok in ('|', '||'):
            left = beats_per_measure - measure_beats
            if left < -1e-6:
                raise ValueError(f'第 {tok} 前小节超拍，请检查节奏')
            if left > 1e-6:                               # 自动补足不足拍
                pad_ticks = int(left * TICKS_PER_BEAT)
                delta_melody += pad_ticks
                cur_tick += pad_ticks
            measure_beats = 0
            i += 1
            continue

        # ── 休止 ──
        m_rest = _REST_RE.match(tok)
        if m_rest:
            beats = _beats(m_rest.group('dur') or '', m_rest.group('dots'), unit)
            ticks = int(beats * TICKS_PER_BEAT)
            delta_melody += ticks
            cur_tick += ticks
            measure_beats += beats
            i += 1
            continue

        # ── 和弦 / O ──
        if _CHORD_RE.match(tok) and not _NOTE_RE.match(tok):
            # 先关掉旧和弦
            if chord_active:
                first = True
                dt = cur_tick - chord_last_tick
                for p in chord_active:
                    chords.append(Message('note_off', channel=CHORD_CH,
                                          note=p, velocity=0,
                                          time=dt if first else 0))
                    first = False
                chord_last_tick = cur_tick

            # 开启新和弦
            chord_active = parse_chord(tok)
            if chord_active:
                first = True
                dt = cur_tick - chord_last_tick
                for p in chord_active:
                    chords.append(Message('note_on', channel=CHORD_CH,
                                          note=p, velocity=CHORD_VELOCITY,
                                          time=dt if first else 0))
                    first = False
                chord_last_tick = cur_tick
            i += 1
            continue

        # ── 音符 ──
        m = _NOTE_RE.match(tok)
        if not m:
            raise ValueError(f'无法解析标记: {tok}')

        deg = int(m.group('deg')); acc = m.group('acc')
        oct_shift = m.group('oct').count("'") - m.group('oct').count(',')
        beats = _beats(m.group('dur') or '', m.group('dots'), unit)
        pitch = _degree2midi(deg, acc, oct_shift, key_root)

        # 连音线合并
        j = i
        tie = bool(m.group('tie'))
        while tie and j + 1 < n:
            nxt = _NOTE_RE.match(toks[j + 1])
            if not nxt:
                break
            n_deg = int(nxt.group('deg')); n_acc = nxt.group('acc')
            n_oct = nxt.group('oct').count("'") - nxt.group('oct').count(',')
            if _degree2midi(n_deg, n_acc, n_oct, key_root) != pitch:
                break
            beats += _beats(nxt.group('dur') or '', nxt.group('dots'), unit)
            tie = bool(nxt.group('tie'))
            j += 1

        ticks = int(beats * TICKS_PER_BEAT)
        melody.append(Message('note_on', note=pitch, velocity=64, time=delta_melody))
        melody.append(Message('note_off', note=pitch, velocity=64, time=ticks))
        delta_melody = 0
        cur_tick += ticks
        measure_beats += beats
        i = j + 1

    # 收尾：补足最后一个小节
    left = beats_per_measure - measure_beats
    if left < -1e-6:
        raise ValueError('最后小节超拍，请检查节奏')
    if left > 1e-6:
        pad_ticks = int(left * TICKS_PER_BEAT)
        melody.append(Message('note_off', note=0, velocity=0, time=pad_ticks))
        cur_tick += pad_ticks

    # 关掉末尾和弦
    if chord_active:
        first = True
        dt = cur_tick - chord_last_tick
        for p in chord_active:
            chords.append(Message('note_off', channel=CHORD_CH,
                                  note=p, velocity=0,
                                  time=dt if first else 0))
            first = False

    melody.append(MetaMessage('end_of_track', time=0))
    chords.append(MetaMessage('end_of_track', time=0))

    # ── 节拍器轨 ──
    if metro_on:
        click = MidiTrack(); mid.tracks.append(click)
        click.append(MetaMessage('track_name', name='Metronome', time=0))
        beat_ticks = int(TICKS_PER_BEAT * 4 / ts_den)
        click_len = int(beat_ticks * .2)
        t = beat_idx = 0
        while t < cur_tick:
            dt = 0 if t == 0 else beat_ticks - click_len
            vel = metro_vel[0] if beat_idx % ts_num == 0 else metro_vel[1]
            click.append(Message('note_on', channel=METRO_CH,
                                 note=METRO_NOTE, velocity=vel, time=dt))
            click.append(Message('note_off', channel=METRO_CH,
                                 note=METRO_NOTE, velocity=0, time=click_len))
            t += beat_ticks; beat_idx += 1
        click.append(MetaMessage('end_of_track', time=0))

    mid.save(outfile)
    return outfile

# ──────────── 实时播放 (可选) ──────────────────────────────
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

# ──────────── CLI ────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)

    txt = Path(sys.argv[1])
    out = (Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith('-')
           else txt.with_suffix('.mid'))

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
                print('节拍器力度应为 "A,R"，已用默认 90,60')

    parser = ScoreParser(txt.read_text(encoding='utf-8'))
    mid_path = build_midi(parser, out, metro_on, metro_vel)
    print(f"✓ 已生成 MIDI: {mid_path}")
    play_midi(mid_path)


if __name__ == "__main__":
    main()