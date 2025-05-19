

from constants import NOTE2MIDI


class Chord:
    def __init__(self, chord:str):
        self.chord = chord
        self.notes = _parse_chord(chord)

    def __getitem__(self, item):
        octave = 0
        while item >= len(self.notes):
            item = item - len(self.notes)
            octave += 1
        return self.notes[item] + octave * 12
    
    def __iter__(self):
        return iter(self.notes)

    def __len__(self):
        return len(self.notes)


def _parse_chord(sym: str) -> list[int]:
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


