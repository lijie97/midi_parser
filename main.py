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
# tie         = "^" ;
# rest        = "R" | "0" ;
"""

import re
import sys
import time
from pathlib import Path

from mido import MidiFile, MidiTrack, Message, MetaMessage, bpm2tempo
from constants import *
from parse_chord import Chord
from utils import _beats, _norm_key, _degree2midi
from reutils import _NOTE_RE, _REST_RE, _CHORD_RE
from chord_patterns import get_chord_pattern


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
               metro_vel: tuple[int, int] = (90, 60),
               from_measure: int = 0,
               to_measure: int = None) -> Path:

    unit = DUR2BEAT.get(parser.meta.get('UNIT', 'q').lower(), 1)
    tempo_bpm = int(parser.meta.get('TEMPO', 120))
    key_root = _norm_key(parser.meta.get('KEY', 'C'))
    if key_root not in NOTE2MIDI:
        raise ValueError(f'#KEY 不支持: {key_root}')

    # 获取拍号
    ts_num, ts_den = (int(x) for x in parser.meta.get('TIME', '4/4').split('/'))
    if ts_den & (ts_den - 1):
        raise ValueError('#TIME 分母必须是 2 的幂')
    beats_per_measure = ts_num * 4 / ts_den   # 四分音拍为 1
    
    # 获取和弦模式
    chord_pattern_name = parser.meta.get('CHORD_PATTERN', 'block')
    
    # 为节奏型分解和弦传递拍号信息
    chord_pattern = get_chord_pattern(chord_pattern_name, time_signature=(ts_num, ts_den))

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
    current_measure = 1     # 当前小节号
    is_recording = from_measure <= 1  # 是否正在记录

    # 添加跟踪小节的开始位置变量
    measure_start_tick = 0     # 当前小节的起始时间

    measure_total_ticks = beats_per_measure * TICKS_PER_BEAT
    

    chord_active = []
    chord_last_tick = 0
    current_chord_pattern = chord_pattern

    toks = parser.tokens
    i, n = 0, len(toks)
    while i < n:
        tok = toks[i]
        
        # 检查是否需要结束记录
        if to_measure is not None and current_measure > to_measure:
            break
        
        # ── 元数据行 ──
        if tok.startswith('#'):
            if 'CHORD_PATTERN' in tok.upper():
                try:
                    _, pattern_name = tok[1:].split('=', 1)
                    current_chord_pattern = get_chord_pattern(pattern_name.strip())
                except Exception as e:
                    print(f"警告：无法解析和弦模式设置 {tok}: {e}")
            i += 1
            continue

        # ── 小节线 ──
        if tok in ('|', '||'):
            left = beats_per_measure - measure_beats
            if left < -1e-6:
                raise ValueError(f'第 {current_measure} 小节超拍，请检查节奏')
            if left > 1e-6 and is_recording:   # 只在记录时补充
                pad_ticks = int(left * TICKS_PER_BEAT)
                delta_melody += pad_ticks
                cur_tick += pad_ticks
            # 更新小节开始位置
            measure_start_tick = cur_tick
            measure_beats = 0
            current_measure += 1
            # 检查是否开始记录
            is_recording = current_measure >= from_measure
            i += 1
            continue

        # 如果不在记录范围内，跳过处理
        if not is_recording:
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
                dt = max(0, cur_tick - chord_last_tick)  # 确保时间为非负
                for p in chord_active:
                    chords.append(Message('note_off', channel=CHORD_CH,
                                          note=p, velocity=0,
                                          time=dt if first else 0))
                    first = False
                chord_last_tick = cur_tick

            # 计算新和弦持续时间
            chord_start_tick = cur_tick
            
            # 查找下一个和弦或曲末以计算持续时间
            j = i + 1
            found_next_chord = False
            
            while j < n:
                next_tok = toks[j]
                if next_tok.startswith('#'):  # 跳过元数据行
                    j += 1
                    continue
                if _CHORD_RE.match(next_tok) and not _NOTE_RE.match(next_tok):
                    found_next_chord = True
                    break
                j += 1
            
            # 计算持续时间
            chord_duration_ticks = 0
            
            # 如果找到下一个和弦，计算两者之间的时间间隔
            if found_next_chord:
                temp_tick = cur_tick
                k = i + 1
                while k < j:
                    tok_k = toks[k]
                    if tok_k.startswith('#') or tok_k in ('|', '||'):
                        k += 1
                        continue
                    
                    m_rest_k = _REST_RE.match(tok_k)
                    if m_rest_k:
                        beats_k = _beats(m_rest_k.group('dur') or '', m_rest_k.group('dots'), unit)
                        temp_tick += int(beats_k * TICKS_PER_BEAT)
                        k += 1
                        continue
                    
                    m_k = _NOTE_RE.match(tok_k)
                    if m_k:
                        beats_k = _beats(m_k.group('dur') or '', m_k.group('dots'), unit)
                        tie_k = bool(m_k.group('tie'))
                        # 连音线合并
                        l = k
                        while tie_k and l + 1 < j:
                            nxt_k = _NOTE_RE.match(toks[l + 1])
                            if not nxt_k:
                                break
                            beats_k += _beats(nxt_k.group('dur') or '', nxt_k.group('dots'), unit)
                            tie_k = bool(nxt_k.group('tie'))
                            l += 1
                        temp_tick += int(beats_k * TICKS_PER_BEAT)
                        k = l + 1
                        continue
                    
                    k += 1  # 处理其他类型的标记
                
                chord_duration_ticks = max(1, temp_tick - chord_start_tick)
            else:
                # 如果是最后一个和弦，使用默认持续时间，比如1小节
                chord_duration_ticks = max(1, int(beats_per_measure * TICKS_PER_BEAT))

            # 解析和弦并使用模式生成事件
            new_chord_notes = Chord(tok)
            if new_chord_notes:
                # 计算前一个和弦和当前和弦在小节内的相对位置
                from_tick = 0.0  # 默认从头开始
                to_tick = 1.0    # 默认到尾结束
                
                # 如果找到了下一个和弦，计算相对位置
                if found_next_chord:
                    # 当前和弦在小节内的位置
                    measure_beats_total = beats_per_measure * TICKS_PER_BEAT
                    
                    # 计算当前和弦开始相对于小节开始的位置
                    if chord_start_tick >= measure_start_tick:
                        chord_offset = chord_start_tick - measure_start_tick
                        from_tick = min(1.0, chord_offset / measure_beats_total)
                    
                    # 计算下一个和弦开始的时间点（当前和弦结束的时间点）
                    next_chord_tick = chord_start_tick + chord_duration_ticks
                    
                    # 如果下一个和弦在当前小节内，调整结束位置
                    if next_chord_tick < measure_start_tick + measure_beats_total:
                        next_offset = next_chord_tick - measure_start_tick
                        to_tick = min(1.0, next_offset / measure_beats_total)
                    
                    # 仅在开发环境启用调试信息
                    if 'DEBUG' in parser.meta and parser.meta['DEBUG'].lower() in ('true', 'yes', '1'):
                        print(f"调试：和弦 {tok}，start={chord_start_tick}，duration={chord_duration_ticks}，"
                              f"measure_start={measure_start_tick}，from_tick={from_tick:.2f}，to_tick={to_tick:.2f}")
                
                chord_last_tick = current_chord_pattern.generate_events(
                    new_chord_notes,
                    chord_start_tick,
                    measure_total_ticks,
                    chords,
                    chord_last_tick,
                    from_tick,
                    to_tick
                )
                chord_active = new_chord_notes
            else:
                chord_active = []
            
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
        raise ValueError(f'第 {current_measure} 小节超拍，请检查节奏')
    if left > 1e-6:
        pad_ticks = int(left * TICKS_PER_BEAT)
        melody.append(Message('note_off', note=0, velocity=0, time=pad_ticks))
        cur_tick += pad_ticks
        # 更新小节开始位置（虽然已经结束，但保持一致性）
        measure_start_tick = cur_tick

    # 关掉末尾和弦
    if chord_active:
        first = True
        dt = max(0, cur_tick - chord_last_tick)  # 确保时间为非负
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
    out = None
    metro_on = False
    metro_vel = (90, 60)
    from_measure = 0
    to_measure = None
    should_play = False

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ('-m', '--metronome', '--metro'):
            metro_on = True
        elif arg.startswith('--metronome=') or arg.startswith('--metro='):
            metro_on = True
            try:
                acc, reg = (int(x) for x in arg.split('=', 1)[1].split(','))
                metro_vel = (max(0, min(acc, 127)), max(0, min(reg, 127)))
            except ValueError:
                print('节拍器力度应为 "A,R"，已用默认 90,60')
        elif arg == '-p':
            should_play = True
        elif arg == '-f':
            if i + 1 < len(sys.argv):
                try:
                    from_measure = int(sys.argv[i + 1])
                    i += 1
                except ValueError:
                    print('起始小节应为整数，已用默认值 0')
        elif arg == '-t':
            if i + 1 < len(sys.argv):
                try:
                    to_measure = int(sys.argv[i + 1])
                    i += 1
                except ValueError:
                    print('结束小节应为整数')
        elif arg == '-o':
            if i + 1 < len(sys.argv):
                out = Path(sys.argv[i + 1])
                i += 1
        elif not arg.startswith('-') and out is None:
            out = Path(arg)
        i += 1

    if out is None:
        out = txt.with_suffix('.mid')

    parser = ScoreParser(txt.read_text(encoding='utf-8'))
    mid_path = build_midi(parser, out, metro_on, metro_vel, from_measure, to_measure)
    print(f"✓ 已生成 MIDI: {mid_path}")
    if should_play:
        play_midi(mid_path)


if __name__ == "__main__":
    main()