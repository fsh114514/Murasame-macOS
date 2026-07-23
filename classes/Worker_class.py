import json
import tempfile
import time
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QGuiApplication

from tool.cloud_API_chat import cloud_portrait, cloud_translate, cloud_talk, cloud_emotion
from tool.config import get_config
from tool.chat import qwen3_lora, ollama_qwen3_sentence, ollama_qwen3_portrait, gpt_sovits_tts, ollama_qwen3_emotion, ollama_qwen3_translate

portrait_type = get_config("./config.json")['portrait']


def list_camera_sources(max_devices=8):
    """返回 macOS 当前摄像头名称及其 AVFoundation/OpenCV 编号。

    OpenCV 的裸编号本身没有设备名称，且 Continuity Camera 连接后编号顺序
    可能变化。macOS 上优先通过 AVFoundation 查询真实名称；查询失败时只
    返回明确标注为未知的候选编号，不再猜测哪个编号是内置摄像头。
    """
    if sys.platform == "darwin":
        try:
            import objc
            from Foundation import NSBundle

            bundle = NSBundle.bundleWithPath_(
                "/System/Library/Frameworks/AVFoundation.framework"
            )
            if bundle is not None:
                bundle.load()
            capture_device = objc.lookUpClass("AVCaptureDevice")
            devices = capture_device.devicesWithMediaType_("vide") or []
            sources = []
            for index, device in enumerate(devices):
                name = str(device.localizedName() or f"摄像头 {index}").strip()
                sources.append((index, name))
            if sources:
                return sources
        except Exception as exc:
            print(f"[AIpet][camera] 获取设备名称失败，将使用未知编号: {exc}")

    return [
        (index, f"OpenCV 摄像头 {index}（设备名称不可用）")
        for index in range(max_devices)
    ]


class qwen3_lora_Worker(QThread):
    finished = pyqtSignal(list, list, list, list, list)  # 返回 (AI回复, history)

    def __init__(self, history, portrait_history, user_input, role="user", t = False):
        super().__init__()
        self.history = history
        self.portrait_history = portrait_history
        self.user_input = user_input
        self.role = role
        self.t = t
        self.force_stop = False

    def stop_all(self):
    
        self.force_stop = True

    def stop_screen(self):
      
        if self.t:
            self.force_stop = True
    def run(self):
        def to_list(text):
            try:
                text = json.loads(text)  # 把字符串解析成 Python 列表
            except Exception as e:
                text = [text]  # 如果解析失败，就退化成单句
            return text
        if self.force_stop:
            print("[qwen3-lora] 已中断生成。")
            return
        reply, history = qwen3_lora(self.history, self.user_input, self.role)  # 对话
        if self.force_stop:print("[ollama-qwn3] 已中断生成。");return
        reply = ollama_qwen3_sentence(reply)  # 句子分割
        if self.force_stop: print("[ollama-qwn3] 已中断生成。");return
        history[-1]["content"] = reply
        portrait_list, portrait_history = ollama_qwen3_portrait(reply, self.portrait_history, portrait_type)  # 立绘
        if self.force_stop: print("[ollama-qwn3] 已中断生成。");return
        emotion_list = ollama_qwen3_emotion(history)  # 情感
        if self.force_stop: print("[ollama-qwn3] 已中断生成。");return
        translate = ollama_qwen3_translate(reply)  # 翻译

        translate = to_list(translate)
        reply = to_list(reply)
        emotion_list = to_list(emotion_list)
        portrait_list = to_list(portrait_list)

        # 并发执行所有TTS任务
        voices = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            # 提交所有TTS任务
            tts_futures = []
            for text, emotion in zip(translate, emotion_list):
                if self.force_stop: print("[tts] 已中断生成。");return
                future = executor.submit(gpt_sovits_tts, text, emotion)
                tts_futures.append(future)

            # 按顺序获取结果，保持原顺序
            for future in tts_futures:
                if self.force_stop: print("[tts] 已中断生成。");return
                voices.append(future.result())

        self.finished.emit(reply, portrait_list, history, portrait_history, voices)  # 发回主线程

class cloud_API_Worker(QThread):
    finished = pyqtSignal(list, list, list, list, list)

    def __init__(self, history, portrait_history, user_input, role="user", t = False):
        super().__init__()
        self.history = history
        self.portrait_history = portrait_history
        self.user_input = user_input
        self.role = role
        self.force_stop = False
        self.t = t

    def stop_all(self):
        """外部调用，用于请求线程中断"""
        self.force_stop = True
    def stop_screen(self):
        """外部调用，用于请求线程中断"""
        if self.t:
            self.force_stop = True
    '''
    这种定义方法来实现中途中断的操作我之前一直没有想到，这个做法很好
    '''
    def run(self):
        def to_list(text):
            try:
                text = json.loads(text)  # 把字符串解析成 Python 列表
            except Exception as e:
                text = [text]  # 如果解析失败，就退化成单句
            return text

        # 1. 先获取对话回复（这个必须串行，因为依赖前面的历史）
        if self.force_stop:print("[deepseek] 已中断生成。");return
        reply, history = cloud_talk(self.history, self.user_input, self.role)
        # 2. 使用线程池并发执行所有 DeepSeek 任务和 TTS 任务
        if self.force_stop:print("[deepseek] 已中断生成。");return
        with ThreadPoolExecutor(max_workers=5) as executor:  # 增加线程数
            # 提交所有任务
            future_portrait = executor.submit(cloud_portrait, reply, self.portrait_history, portrait_type)
            future_translate = executor.submit(cloud_translate, reply)
            future_emotion = executor.submit(cloud_emotion, history)

            # 获取所有结果
            portrait_result, portrait_history = future_portrait.result()
            emotion_result = future_emotion.result()
            translate_result = future_translate.result()

        # 3. 处理结果

        translate_list = to_list(translate_result)
        emotion_list = to_list(emotion_result)
        portrait_list = to_list(portrait_result)
        reply_list = to_list(reply)
        # 4. 并发执行所有TTS任务
        voices = []
        with ThreadPoolExecutor(max_workers=3) as tts_executor:
            # 提交所有TTS任务
            tts_futures = []
            for text, emotion in zip(translate_list, emotion_list):
                if self.force_stop:print("[tts] 已中断生成。");return
                future = tts_executor.submit(gpt_sovits_tts, text, emotion)
                tts_futures.append(future)

            # 按顺序获取结果，保持原顺序
            for future in tts_futures:
                if self.force_stop:print("[tts] 已中断生成。");return
                voices.append(future.result())

        self.finished.emit(reply_list, portrait_list, history, portrait_history, voices)


screen_index = get_config("./config.json")["screen_index"]
class ScreenWorker(QThread):
    # 发出临时文件路径（主线程负责删除）
    screenshot_captured = pyqtSignal(str)

    def __init__(self, interval_sec=3.0, parent=None):
        super().__init__(parent)
        self.interval = interval_sec
        os.makedirs("tmp", exist_ok=True)

    def run(self):
        screens = QGuiApplication.screens()
        screen = screens[screen_index]
        if screen is None:
            return
        while not self.isInterruptionRequested():
            # 抓屏（全屏）
            pixmap = screen.grabWindow(0)
            # 存到临时文件
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png", dir="tmp")
            tmp_name = tmp.name
            tmp.close()
            pixmap.save(tmp_name, "PNG")
            # 发信号，让主线程去处理（网络调用等）
            self.screenshot_captured.emit(tmp_name)
            # sleep 可被 requestInterruption() 打断（间隔相对宽松）
            for _ in range(int(self.interval * 10)):
                if self.isInterruptionRequested():
                    break
                time.sleep(0.1)


class CameraWorker(QThread):
    """定时从摄像头抓取单帧，不保存连续视频。"""

    camera_captured = pyqtSignal(str)

    def __init__(self, interval_sec=300.0, camera_index=0, initial_delay_sec=0.0, parent=None):
        super().__init__(parent)
        self.interval = max(1.0, float(interval_sec))
        self.camera_index = int(camera_index)
        self.initial_delay = max(0.0, float(initial_delay_sec))
        os.makedirs("tmp", exist_ok=True)

    def run(self):
        import cv2

        for _ in range(int(self.initial_delay * 10)):
            if self.isInterruptionRequested():
                return
            time.sleep(0.1)

        # 按需打开摄像头：拍完一帧立即释放，等待期间不占用摄像头。
        while not self.isInterruptionRequested():
            camera = None
            try:
                backend = (
                    cv2.CAP_AVFOUNDATION
                    if sys.platform == "darwin"
                    else cv2.CAP_ANY
                )
                camera = cv2.VideoCapture(self.camera_index, backend)
                if not camera.isOpened():
                    print(f"[AIpet][camera] 无法打开摄像头 index={self.camera_index}")
                else:
                    ok, frame = camera.read()
                    if ok and frame is not None:
                        tmp = tempfile.NamedTemporaryFile(
                            delete=False, suffix=".jpg", dir="tmp"
                        )
                        tmp_name = tmp.name
                        tmp.close()
                        if cv2.imwrite(tmp_name, frame):
                            self.camera_captured.emit(tmp_name)
                        else:
                            try:
                                os.remove(tmp_name)
                            except OSError:
                                pass
                    else:
                        print("[AIpet][camera] 读取摄像头画面失败")
            except Exception as exc:
                # 摄像头权限、设备切换或 OpenCV 后端异常不应带崩整个桌宠。
                print(f"[AIpet][camera] 本轮读取异常，已跳过: {exc}")
            finally:
                # 无论读取成功与否，都在本轮结束时释放摄像头句柄。
                if camera is not None:
                    try:
                        camera.release()
                    except Exception as exc:
                        print(f"[AIpet][camera] 释放摄像头失败: {exc}")

            for _ in range(int(self.interval * 10)):
                if self.isInterruptionRequested():
                    break
                time.sleep(0.1)
