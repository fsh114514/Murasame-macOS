import sys
import threading
import json
import os
import subprocess
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


def configure_macos_spaces() -> None:
    """让丛雨窗口加入所有 macOS Space，并允许显示在全屏应用上方。"""
    if sys.platform != "darwin":
        return
    try:
        import AppKit

        NSApp = AppKit.NSApp
        NSWindowCollectionBehaviorCanJoinAllSpaces = (
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        )
        NSWindowCollectionBehaviorCanJoinAllApplications = getattr(
            AppKit,
            "NSWindowCollectionBehaviorCanJoinAllApplications",
            # macOS 26 supports this flag, but older/bundled PyObjC versions
            # may not export the AppKit constant.  Do not fall back to
            # FullScreenAuxiliary: that flag does not cover other apps'
            # fullscreen Spaces.
            1 << 18,
        )
        NSWindowCollectionBehaviorFullScreenAuxiliary = getattr(
            AppKit, "NSWindowCollectionBehaviorFullScreenAuxiliary", 1 << 8
        )
        # 普通 floating level 在某些 macOS 全屏应用下会被压到后面；
        # status level 是 macOS 给悬浮工具/系统覆盖层使用的层级。
        NSOverlayWindowLevel = getattr(
            AppKit,
            "NSPopUpMenuWindowLevel",
            getattr(AppKit, "NSStatusWindowLevel", 25),
        )

        behavior = (
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorCanJoinAllApplications
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        # 不要过滤 isVisible：切换全屏时 macOS 可能先把 QNSWindow 标记为
        # 不可见，但窗口对象仍然存在，必须继续对它施加全屏行为并重新置前。
        windows = [
            window
            for window in NSApp.windows()
            if window.title() == "丛雨" or window.className() == "QNSWindow"
        ]
        # 原生兼容面板负责真正全屏时的显示；此时不要把已隐藏的 Qt
        # 窗口重新 orderFront，否则会再次出现两个丛雨。
        native_fullscreen = getattr(configure_macos_spaces, "_native_fullscreen", False)
        if native_fullscreen and getattr(configure_macos_spaces, "_native_active", False):
            return
        for window in windows:
            window.setCollectionBehavior_(behavior)
            window.setLevel_(NSOverlayWindowLevel)
            window.setHidesOnDeactivate_(False)
            window.setCanHide_(False)
            if not window.isVisible():
                window.orderFrontRegardless()
        state = (len(windows), behavior, NSOverlayWindowLevel)
        if getattr(configure_macos_spaces, "_last_state", None) != state:
            details = "; ".join(
                f"title={window.title()!r}, class={window.className()}, "
                f"level={window.level()}, behavior={window.collectionBehavior()}"
                for window in windows
            )
            print(
                f"[AIpet] 已为 {len(windows)} 个窗口启用跨 Space 和全屏显示 "
                f"(behavior={behavior}, level={NSOverlayWindowLevel}); {details}"
            )
            configure_macos_spaces._last_state = state
    except Exception as exc:
        print(f"[AIpet] 跨 Space 设置失败，保留普通置顶: {exc}")


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
    QTimer.singleShot(500, configure_macos_spaces)
    QTimer.singleShot(2000, configure_macos_spaces)
    spaces_timer = QTimer(pet)
    spaces_timer.setInterval(1000)
    spaces_timer.timeout.connect(configure_macos_spaces)
    spaces_timer.start()

    native_overlay_state = {
        "process": None,
        "qt_was_visible": True,
        "fullscreen": False,
        "command_mtime": 0,
    }

    native_overlay_state_path = Path("tmp/native_overlay_fullscreen.state")
    native_overlay_qt_path = Path("tmp/native_overlay_qt_visible.state")
    native_overlay_command_path = Path("tmp/native_overlay_command.txt")

    def save_native_qt_visibility() -> None:
        try:
            native_overlay_qt_path.parent.mkdir(parents=True, exist_ok=True)
            native_overlay_qt_path.write_text(
                "1\n" if pet.isVisible() else "0\n", encoding="utf-8"
            )
        except OSError:
            pass

    def sync_native_overlay_visibility() -> None:
        """根据原生面板检测到的全屏状态切换 Qt 交互窗口。"""
        process = native_overlay_state["process"]
        if process is None or process.poll() is not None:
            return
        save_native_qt_visibility()
        try:
            command_stat = native_overlay_command_path.stat()
            command_mtime = command_stat.st_mtime_ns
            if command_mtime > native_overlay_state["command_mtime"]:
                command = native_overlay_command_path.read_text(encoding="utf-8").strip()
                native_overlay_state["command_mtime"] = command_mtime
                if command:
                    native_overlay_command_path.write_text("", encoding="utf-8")
                    if command == "__native_overlay_disable__":
                        native_overlay_action.setChecked(False)
                    else:
                        # 原生面板不经过 Qt 的 focusIn/focusOut 事件；输入
                        # 前主动解除输入暂停，确保全屏时仍能生成回复。
                        print("[AIpet][native-overlay] 收到全屏输入，开始生成回复")
                        pet.resume_all_ai()
                        pet.start_thread(command, role="user")
        except (OSError, UnicodeError):
            pass
        try:
            fullscreen = native_overlay_state_path.read_text(encoding="utf-8").strip() == "1"
        except (OSError, UnicodeError):
            fullscreen = False
        if fullscreen == native_overlay_state["fullscreen"]:
            return
        native_overlay_state["fullscreen"] = fullscreen
        configure_macos_spaces._native_fullscreen = fullscreen
        if fullscreen:
            # 隐藏 Qt 窗口不会稳定地产生 focusOutEvent。先恢复自动行为，
            # 再切换到原生面板，避免全屏后屏幕/摄像头读取被遗留为暂停。
            pet.resume_all_ai()
            pet.hide()
        else:
            pet.show()
            pet.resume_all_ai()

    def set_native_overlay_enabled(enabled: bool) -> None:
        process = native_overlay_state["process"]
        if enabled:
            if process is not None and process.poll() is None:
                return
            overlay_binary = Path(".native_overlay/murasame_overlay")
            overlay_image = Path("tmp/native_overlay_portrait.png")
            if not overlay_binary.exists() or not overlay_image.exists():
                print("[AIpet][native-overlay] 原生面板文件不存在，请重新部署原生组件")
                native_overlay_action.setChecked(False)
                return
            try:
                native_overlay_state["qt_was_visible"] = pet.isVisible()
                # 普通桌面保留 Qt 窗口作为交互层；只有原生面板检测到真正
                # 的全屏窗口后，sync_native_overlay_visibility 才会隐藏它。
                save_native_qt_visibility()
                native_overlay_command_path.parent.mkdir(parents=True, exist_ok=True)
                native_overlay_command_path.write_text("", encoding="utf-8")
                process = subprocess.Popen(
                    [
                        str(overlay_binary.resolve()),
                        str(overlay_image.resolve()),
                        str(Path("tmp/native_overlay_text.txt").resolve()),
                        str(native_overlay_state_path.resolve()),
                        str(native_overlay_qt_path.resolve()),
                        str(native_overlay_command_path.resolve()),
                    ],
                    cwd=str(Path.cwd()),
                )
                native_overlay_state["process"] = process
                native_overlay_state["fullscreen"] = False
                configure_macos_spaces._native_active = True
                configure_macos_spaces._native_fullscreen = False
                print("[AIpet][native-overlay] 已启用原生全屏兼容模式")
            except Exception as exc:
                native_overlay_action.setChecked(False)
                print(f"[AIpet][native-overlay] 启动失败: {exc}")
        else:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            native_overlay_state["process"] = None
            native_overlay_state["fullscreen"] = False
            configure_macos_spaces._native_active = False
            configure_macos_spaces._native_fullscreen = False
            if native_overlay_state["qt_was_visible"]:
                pet.show()
            print("[AIpet][native-overlay] 已恢复 Qt 桌宠窗口")

    def stop_native_overlay() -> None:
        set_native_overlay_enabled(False)

    app.aboutToQuit.connect(stop_native_overlay)
    native_overlay_timer = QTimer(pet)
    native_overlay_timer.setInterval(400)
    native_overlay_timer.timeout.connect(sync_native_overlay_visibility)
    native_overlay_timer.start()

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
                if camera_index == 0:
                    label = "iPhone（OpenCV 编号 0）"
                elif camera_index == 1:
                    label = "内置摄像头（OpenCV 编号 1）"
                else:
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

    native_overlay_action = QAction("原生全屏兼容模式")
    native_overlay_action.setCheckable(True)
    native_overlay_action.toggled.connect(set_native_overlay_enabled)

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
    tray_menu.addAction(native_overlay_action)
    tray_menu.addAction(clear_action)
    tray_menu.addSeparator()
    tray_menu.addAction(memory_action)
    tray_menu.addAction(view_memory_action)
    tray_menu.addAction(clear_memory_action)
    tray_menu.addAction(exit_action)
    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()

    # 默认启用原生全屏兼容模式；菜单仍可随时关闭并恢复普通 Qt 桌宠。
    QTimer.singleShot(800, lambda: native_overlay_action.setChecked(True))

    sys.exit(app.exec_())  # 进入事件循环
