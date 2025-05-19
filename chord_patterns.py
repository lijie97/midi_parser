from typing import List, Tuple
import mido
from constants import TICKS_PER_BEAT, CHORD_CH, CHORD_VELOCITY

class ChordPattern:
    """和弦模式基类，定义了和弦演奏模式的接口"""
    
    def __init__(self, velocity: int = CHORD_VELOCITY):
        self.velocity = velocity
    
    def generate_events(self, 
                        chord_notes: List[int], 
                        start_tick: int, 
                        duration_ticks: int, 
                        track: mido.MidiTrack,
                        last_tick: int) -> int:
        """
        生成和弦的MIDI事件
        
        Args:
            chord_notes: 和弦音符列表
            start_tick: 和弦开始的绝对tick
            duration_ticks: 和弦持续的tick数
            track: 要添加事件的MIDI轨道
            last_tick: 上一个和弦事件的时间
            
        Returns:
            最后一个和弦事件的绝对tick
        """
        raise NotImplementedError("子类必须实现此方法")


class BlockChordPattern(ChordPattern):
    """柱式和弦 - 所有音符同时演奏"""
    
    def generate_events(self, 
                        chord_notes: List[int], 
                        start_tick: int, 
                        duration_ticks: int, 
                        track: mido.MidiTrack,
                        last_tick: int) -> int:
        if not chord_notes:
            return last_tick
            
        # 确保时间值为非负数
        dt = max(0, start_tick - last_tick)
        duration_ticks = max(1, duration_ticks)
            
        # 和弦音符同时开始
        first = True
        for note in chord_notes:
            track.append(mido.Message('note_on', channel=CHORD_CH,
                                      note=note, velocity=self.velocity,
                                      time=dt if first else 0))
            first = False
        
        # 和弦音符同时结束
        first = True
        for note in chord_notes:
            track.append(mido.Message('note_off', channel=CHORD_CH,
                                      note=note, velocity=0,
                                      time=duration_ticks if first else 0))
            first = False
            
        return start_tick + duration_ticks


class ArpeggioChordPattern(ChordPattern):
    """分解和弦 - 音符依次演奏"""
    
    def __init__(self, velocity: int = CHORD_VELOCITY):
        """
        Args:
            velocity: 音符力度
        """
        super().__init__(velocity)
    
    def generate_events(self, 
                        chord_notes: List[int], 
                        start_tick: int, 
                        duration_ticks: int, 
                        track: mido.MidiTrack,
                        last_tick: int) -> int:
        if not chord_notes:
            return last_tick
        
        # 确保时间值为非负数
        dt = max(0, start_tick - last_tick)
        duration_ticks = max(1, duration_ticks)
        
        # 计算每个音符的持续时间
        note_count = len(chord_notes)
        if note_count == 0:
            return last_tick
        
        # 根据和弦总时长和音符数量确定每个音符的时长
        single_note_duration = duration_ticks // note_count
        
        # 第一个音符的延迟
        current_time = dt
        
        # 添加所有音符事件 - 一个接一个地发声与结束
        current_tick = start_tick
        for i, note in enumerate(chord_notes):
            # 添加note_on事件
            track.append(mido.Message('note_on', channel=CHORD_CH,
                                     note=note, velocity=self.velocity,
                                     time=current_time))
            
            # 添加对应的note_off事件
            track.append(mido.Message('note_off', channel=CHORD_CH,
                                     note=note, velocity=0,
                                     time=single_note_duration))
            
            # 重置时间计数，因为我们刚刚使用了绝对时间点之间的差
            current_time = 0
            current_tick += single_note_duration
        
        return current_tick


class GuitarStrumsPattern(ChordPattern):
    """吉他扫弦模式 - 快速顺序演奏，通常从低音到高音"""
    
    def __init__(self, velocity: int = CHORD_VELOCITY, strum_duration: float = 0.05):
        """
        Args:
            velocity: 音符力度
            strum_duration: 扫弦持续时间（以拍为单位）
        """
        super().__init__(velocity)
        self.strum_duration = strum_duration
    
    def generate_events(self, 
                        chord_notes: List[int], 
                        start_tick: int, 
                        duration_ticks: int, 
                        track: mido.MidiTrack,
                        last_tick: int) -> int:
        if not chord_notes:
            return last_tick
        
        # 确保时间值为非负数
        dt = max(0, start_tick - last_tick)
        duration_ticks = max(1, duration_ticks)
            
        # 按音高排序（吉他扫弦通常从低音到高音）
        sorted_notes = sorted(chord_notes)
        note_count = len(sorted_notes)
        
        # 计算扫弦时间
        strum_ticks = int(self.strum_duration * TICKS_PER_BEAT)
        total_strum_ticks = strum_ticks * (note_count - 1) if note_count > 1 else 0
        
        # 确保总时长不变
        note_duration = max(1, duration_ticks - total_strum_ticks)
        
        # 添加所有音符的note_on事件
        for i, note in enumerate(sorted_notes):
            time_val = dt if i == 0 else strum_ticks
            track.append(mido.Message('note_on', channel=CHORD_CH,
                                     note=note, velocity=self.velocity,
                                     time=time_val))
            dt = 0
        
        # 所有音符同时结束
        first = True
        for note in sorted_notes:
            track.append(mido.Message('note_off', channel=CHORD_CH,
                                     note=note, velocity=0,
                                     time=note_duration if first else 0))
            first = False
        
        return start_tick + duration_ticks


# 创建节奏型分解和弦类
class RhythmicArpeggioPattern(ChordPattern):
    """节奏型分解和弦 - 按照指定拍号的节奏模式演奏"""
    
    def __init__(self, velocity: int = CHORD_VELOCITY, time_signature: tuple = (4, 4)):
        """
        Args:
            velocity: 音符力度
            time_signature: 拍号，格式为(分子, 分母)，如(4, 4)表示4/4拍
        """
        super().__init__(velocity)
        self.time_signature = time_signature
    
    def generate_events(self, 
                        chord_notes: List[int], 
                        start_tick: int, 
                        duration_ticks: int, 
                        track: mido.MidiTrack,
                        last_tick: int) -> int:
        if not chord_notes:
            return last_tick
        
        # 确保时间值为非负数
        dt = max(0, start_tick - last_tick)
        duration_ticks = max(1, duration_ticks)
        
        # 获取拍号
        num, den = self.time_signature
        
        # 根据拍号创建合适的节奏模式
        if num == 4 and den == 4:  # 4/4拍
            # 1拍1拍1拍1拍的模式
            pattern = [1, 1, 1, 1]
        elif num == 3 and den == 4:  # 3/4拍
            # 1拍1拍1拍的模式
            pattern = [1, 1, 1]
        elif num == 6 and den == 8:  # 6/8拍
            # 3个8分音符 + 3个8分音符的模式
            pattern = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
        elif num == 2 and den == 4:  # 2/4拍
            # 1拍1拍的模式
            pattern = [1, 1]
        else:
            # 默认平均分配
            pattern = [1] * num
        
        # 计算每个节奏单位的实际tick数
        beat_tick = TICKS_PER_BEAT * 4 / den  # 一拍的tick数
        pattern_ticks = [int(p * beat_tick) for p in pattern]
        total_pattern_ticks = sum(pattern_ticks)
        
        # 循环播放模式直到达到所需的持续时间
        note_idx = 0
        note_count = len(chord_notes)
        current_tick = start_tick
        remaining_ticks = duration_ticks
        
        # 第一个音符的延迟
        current_time = dt
        
        while remaining_ticks > 0:
            # 获取当前节奏单位的tick数
            pattern_idx = note_idx % len(pattern_ticks)
            current_pattern_ticks = min(pattern_ticks[pattern_idx], remaining_ticks)
            
            if current_pattern_ticks <= 0:
                break
                
            # 获取当前音符
            chord_idx = note_idx % note_count
            note = chord_notes[chord_idx]
            
            # 添加note_on事件
            track.append(mido.Message('note_on', channel=CHORD_CH,
                                     note=note, velocity=self.velocity,
                                     time=current_time))
            
            # 添加note_off事件
            track.append(mido.Message('note_off', channel=CHORD_CH,
                                     note=note, velocity=0,
                                     time=current_pattern_ticks))
            
            # 更新计数器
            current_time = 0  # 后续音符紧接着前一个
            remaining_ticks -= current_pattern_ticks
            current_tick += current_pattern_ticks
            note_idx += 1
        
        return current_tick


# 工厂函数，根据模式名称返回对应的模式实例
def get_chord_pattern(pattern_name: str, time_signature: tuple = (4, 4)) -> ChordPattern:
    patterns = {
        "block": BlockChordPattern(),
        "arpeggio": ArpeggioChordPattern(),
        "guitar": GuitarStrumsPattern(),
        "rhythmic": RhythmicArpeggioPattern(time_signature=time_signature),
        # 可以在这里添加更多模式
    }
    
    return patterns.get(pattern_name.lower(), BlockChordPattern()) 