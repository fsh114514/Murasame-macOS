import threading
import wave
from typing import Optional

import numpy as np
import sounddevice as sd


class AudioRecorder:
    """录制一段麦克风音频，不包含任何键盘监听逻辑。"""

    def __init__(self, samplerate: int = 16000, channels: int = 1):
        self.samplerate = samplerate
        self.channels = channels
        self._stream: Optional[sd.InputStream] = None
        self._frames = []
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[AIpet][voice] record status: {status}")
        with self._lock:
            self._frames.append(indata.copy())

    def start(self) -> None:
        with self._lock:
            self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()
        print("[AIpet][voice] 录音开始")

    def stop_and_save(self, wav_path: str) -> Optional[str]:
        if self._stream is None:
            return None
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None

        with self._lock:
            if not self._frames:
                print("[AIpet][voice] 没有录到有效音频")
                return None
            data = np.concatenate(self._frames, axis=0)

        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)
                wf.setframerate(self.samplerate)
                wf.writeframes(data.tobytes())
            print(f"[AIpet][voice] 录音保存: {wav_path}")
            return wav_path
        except Exception as exc:
            print(f"[AIpet][voice] 保存 WAV 失败: {exc}")
            return None
