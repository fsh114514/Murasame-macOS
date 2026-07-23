# 丛雨

这是基于 [kuxiaowo/AIpet-Murasame](https://github.com/kuxiaowo/AIpet-Murasame) 和 [LemonQu-GIT/MurasamePet](https://github.com/LemonQu-GIT/MurasamePet) 的个人 macOS Apple Silicon 适配版。

本版本主要整理和增加了：

- Apple Silicon macOS 启动脚本和本地 GPT-SoVITS 语音合成；
- DeepSeek/Qwen 对话与视觉识别接口；
- 屏幕、摄像头按需读取；
- 长期记忆摘要和短期历史裁剪；
- macOS 置顶、触控板移动、快捷缩放和托盘控制；
- GPT-SoVITS 异常检测与端口清理。

## 发布范围

本仓库只发布源代码、启动脚本、配置模板和文档。以下内容没有随仓库发布：

- API Key、个人聊天记录和长期记忆；
- Python 虚拟环境、模型权重和缓存；
- GPT-SoVITS 第三方源代码；
- 丛雨立绘、语音、图标和其他未确认可再分发的素材。

角色和音频素材的版权不因本代码仓库的开源许可证而自动转移。使用者需要自行确认素材来源和授权。

## macOS 使用前准备

1. 安装 Python 3.10 或更高版本及项目依赖。
2. 按 GPT-SoVITS 官方文档单独安装 GPT-SoVITS 和所需模型。
3. 创建 `.gpt-sovits-venv`，并确保项目目录中存在 `GPT-SoVITS` 文件夹。
4. 复制配置模板：

   ```bash
   cp config.example.json config.json
   ```

5. 在 `config.json` 中填写自己的 API Key 和本地服务地址。
6. 准备自己拥有或明确获准使用的立绘、参考音频、字体等素材。
7. 双击 `start_macos.command` 启动。

请勿把填写过 Key 的 `config.json` 提交到 GitHub。

## 配置

参见 [config.example.json](config.example.json)。其中：

- `model_type: "deepseek"` 使用云端对话；
- `screen_type` 控制屏幕识别；
- `camera_type` 控制摄像头识别；
- `memory_enabled` 控制长期记忆摘要；
- `tts_type: "local"` 使用本地 GPT-SoVITS。

## 上游与许可证

本项目保留上游项目的版权和来源说明，并在此基础上进行修改。修改内容由本仓库维护者负责。

当前仓库代码按 `AGPL-3.0` 发布，详见 [LICENSE](LICENSE)。发布修改版时请保留版权、许可证和修改说明。第三方依赖、模型、字体、图片和音频素材分别适用其各自许可证或授权条款。

## 免责声明

本项目仅供学习和个人研究使用，不提供任何担保。使用屏幕或摄像头识别时，请确认自己了解数据会发送到所配置的视觉模型服务，并遵守相关服务条款及隐私法律。
