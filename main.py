import sys
import threading
import json
import os
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QTimer, QObject, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QAction, QActionGroup, QMenu, QMessageBox

from classes.murasame_class import Murasame
from classes.Worker_class import list_camera_sources
from api import app as api_app
import uvicorn

from tool.config import get_config
from tool.audio_recorder import AudioRecorder


CONFIG = get_config("./config.json")
screen_index = CONFIG["screen_index"]


class VoiceBridge(QObject):
    text_ready = pyqtSignal(str)
    record_start = pyqtSignal()
    record_end = pyqtSignal()


def save_screen_type(pet: Murasame) -> None:
    """在程序退出时保存当前截图开关状态到配置文件"""
    try:
        config_path = "./config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        config["screen_type"] = "true" if pet.is_screenshot_enabled() else "false"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[AIpet] 保存 screen_type 失败: {e}")


if __name__ == "__main__":

    # 后台启动本地 API 服务（FastAPI + Uvicorn）
    def _run_api_server():
        config = uvicorn.Config(api_app, host="0.0.0.0", port=28565, log_level="info")
        server = uvicorn.Server(config)
        server.run()

    api_thread = threading.Thread(
        target=_run_api_server,
        name="uvicorn-thread",
        daemon=True,
    )
    api_thread.start()

    app = QApplication(sys.argv)  # 创建应用对象
    app.setApplicationName("丛雨")
    app.setApplicationDisplayName("丛雨")
    pet = Murasame()  # 创建桌宠实例
    pet.setWindowTitle("丛雨")
    app.aboutToQuit.connect(lambda: save_screen_type(pet))
    pet.show()  # 显示窗口

    screens = QApplication.screens()
    target_screen = screens[screen_index]
    geometry = target_screen.availableGeometry()
    # 首次启动放在目标屏幕右下角，并避开屏幕边缘/Dock 一小段距离。
    margin = 20
    initial_x = geometry.right() - pet.width() - margin + 1
    initial_y = geometry.bottom() - pet.height() - margin + 1
    pet.move(max(geometry.x(), initial_x), max(geometry.y(), initial_y))

    # ===== 手动语音聊天 =====
    # 通过桌宠/托盘菜单启动和停止录音，不监听任何全局键盘。
    bridge = VoiceBridge()
    bridge.text_ready.connect(lambda text: pet.start_thread(text, role="user"))
    bridge.record_start.connect(
        lambda: pet.show_text("正在录音......", typing=False)
    )
    bridge.record_end.connect(
        lambda: pet.show_text("录音结束，正在识别......", typing=False)
    )

    voice_state = {"recording": False, "recorder": None}

    def is_manual_recording() -> bool:
        return voice_state["recording"]

    def start_manual_recording() -> None:
        if voice_state["recording"]:
            return
        try:
            recorder = AudioRecorder()
            recorder.start()
            voice_state["recorder"] = recorder
            voice_state["recording"] = True
            bridge.record_start.emit()
            print("[AIpet][voice] 手动录音开始")
        except Exception as exc:
            voice_state["recorder"] = None
            voice_state["recording"] = False
            print(f"[AIpet][voice] 手动录音启动失败: {exc}")
            pet.show_text("麦克风启动失败，请检查系统权限", typing=False)

    def stop_manual_recording() -> None:
        if not voice_state["recording"]:
            return

        recorder = voice_state["recorder"]
        voice_state["recorder"] = None
        voice_state["recording"] = False
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_path = Path("tmp") / f"manual_{timestamp}.wav"
        wav_path.parent.mkdir(exist_ok=True)

        try:
            saved = recorder.stop_and_save(str(wav_path))
        except Exception as exc:
            saved = None
            print(f"[AIpet][voice] 手动录音保存失败: {exc}")
        bridge.record_end.emit()

        if not saved:
            return

        def transcribe_task() -> None:
            try:
                from tool.stt import transcribe_full

                model_size = CONFIG.get("stt_model", "large-v3")
                text = transcribe_full(
                    saved,
                    model_size=model_size,
                    device="cpu",
                ).strip()
                if text:
                    print(f"[AIpet][voice] 识别文本: {text}")
                    bridge.text_ready.emit(text)
                else:
                    print("[AIpet][voice] 语音识别结果为空")
            except Exception as exc:
                print(f"[AIpet][voice] 语音识别失败: {exc}")
            finally:
                try:
                    if os.path.exists(saved):
                        os.remove(saved)
                except Exception as exc:
                    print(f"[AIpet][voice] 删除临时录音失败: {exc}")

        threading.Thread(target=transcribe_task, daemon=True).start()

    def toggle_manual_recording() -> None:
        if is_manual_recording():
            stop_manual_recording()
        else:
            start_manual_recording()

    def toggle_pet_visibility() -> None:
        if pet.isVisible():
            pet.hide()
        else:
            pet.show()

    pet.set_voice_record_callbacks(
        start_manual_recording,
        stop_manual_recording,
        is_manual_recording,
    )

    tray_icon = QSystemTrayIcon(QIcon("icon.png"), parent=app)
    tray_menu = QMenu()

    # 勿扰模式（勾选 = 开启勿扰，不再主动打扰）
    dnd_action = QAction("Do Not Disturb")
    dnd_action.setCheckable(True)
    dnd_action.setChecked(pet.is_dnd_enabled())
    dnd_action.toggled.connect(pet.set_dnd_enabled)

    # 屏幕截图开关（勾选 = 开启截图）
    screenshot_action = QAction("Screenshot")
    screenshot_action.setCheckable(True)
    screenshot_action.setChecked(pet.is_screenshot_enabled())
    screenshot_action.toggled.connect(pet.set_screenshot_enabled)

    # 摄像头读取开关（默认关闭；开启时 macOS 会请求摄像头权限）
    camera_action = QAction("Camera")
    camera_action.setCheckable(True)
    camera_action.setChecked(pet.is_camera_enabled())
    camera_action.toggled.connect(pet.set_camera_enabled)

    # 摄像头来源选择：显示 macOS 返回的真实设备名，避免把 iPhone 误标为内置摄像头。
    camera_source_menu = QMenu("摄像头来源", tray_menu)
    refresh_camera_sources_action = QAction("刷新摄像头列表", camera_source_menu)

    def rebuild_camera_source_menu():
        try:
            # 先移出固定刷新动作，再清理动态设备动作，避免 Qt 删除仍在使用的 QAction。
            camera_source_menu.removeAction(refresh_camera_sources_action)
            camera_source_menu.clear()
            camera_source_menu.addAction(refresh_camera_sources_action)
            camera_source_menu.addSeparator()

            camera_source_group = QActionGroup(camera_source_menu)
            camera_source_group.setExclusive(True)
            sources = list_camera_sources()
            current_index = pet.get_camera_index()
            if not any(index == current_index for index, _name in sources):
                sources.append((current_index, f"当前配置编号 {current_index}（未检测到名称）"))

            for camera_index, device_name in sources:
                label = f"{device_name}（OpenCV 编号 {camera_index}）"
                source_action = QAction(label, camera_source_menu)
                source_action.setCheckable(True)
                source_action.setData(camera_index)
                source_action.setChecked(current_index == camera_index)
                camera_source_group.addAction(source_action)
                source_action.triggered.connect(
                    lambda _checked, action=source_action: pet.set_camera_index(
                        int(action.data())
                    )
                )

            camera_source_menu.addActions(camera_source_group.actions())
        except Exception as exc:
            print(f"[AIpet][camera] 刷新摄像头菜单失败: {exc}")

    refresh_camera_sources_action.triggered.connect(rebuild_camera_source_menu)
    camera_source_menu.aboutToShow.connect(rebuild_camera_source_menu)
    rebuild_camera_source_menu()

    clear_action = QAction("Clear History")
    clear_action.triggered.connect(pet.cleer_history)

    voice_action = QAction("开始/结束语音录音")
    voice_action.triggered.connect(toggle_manual_recording)

    visibility_action = QAction("显示/隐藏桌宠")
    visibility_action.triggered.connect(toggle_pet_visibility)

    memory_action = QAction("长期记忆")
    memory_action.setCheckable(True)
    memory_action.setChecked(pet.is_memory_enabled())
    memory_action.toggled.connect(pet.set_memory_enabled)

    view_memory_action = QAction("查看长期记忆")
    view_memory_action.triggered.connect(
        lambda: QMessageBox.information(
            pet,
            "丛雨的长期记忆",
            pet.show_memory_summary(),
        )
    )

    clear_memory_action = QAction("清空长期记忆")
    clear_memory_action.triggered.connect(pet.clear_memory_summary)

    # 退出
    exit_action = QAction("Exit")
    exit_action.triggered.connect(app.quit)

    # 菜单绑定
    tray_menu.addAction(dnd_action)
    tray_menu.addAction(screenshot_action)
    tray_menu.addAction(camera_action)
    tray_menu.addMenu(camera_source_menu)
    tray_menu.addAction(voice_action)
    tray_menu.addAction(visibility_action)
    tray_menu.addAction(clear_action)
    tray_menu.addSeparator()
    tray_menu.addAction(memory_action)
    tray_menu.addAction(view_memory_action)
    tray_menu.addAction(clear_memory_action)
    tray_menu.addAction(exit_action)
    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()

    sys.exit(app.exec_())  # 进入事件循环
