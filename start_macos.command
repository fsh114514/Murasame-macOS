#!/bin/zsh

set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "未找到项目虚拟环境，请先完成部署。"
  exit 1
fi

if [[ ! -x ".gpt-sovits-venv/bin/python" ]]; then
  echo "未找到 GPT-SoVITS 虚拟环境，请先完成语音合成部署。"
  exit 1
fi

tts_pid=""
tts_ready="false"

clear_tts_port() {
  local stale_tts_pids
  stale_tts_pids="$(lsof -tiTCP:9880 -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$stale_tts_pids" ]]; then
    echo "清理占用 9880 的旧 GPT-SoVITS 进程..."
    while IFS= read -r stale_tts_pid; do
      [[ -z "$stale_tts_pid" ]] && continue
      kill "$stale_tts_pid" 2>/dev/null || true
    done <<< "$stale_tts_pids"
    for _ in {1..10}; do
      if ! lsof -tiTCP:9880 -sTCP:LISTEN >/dev/null 2>&1; then
        return 0
      fi
      sleep 1
    done
    echo "无法释放 9880 端口，请关闭占用该端口的程序后重试。"
    return 1
  fi
}

tts_smoke_test() {
  curl -fsS --max-time 90 -X POST "http://127.0.0.1:9880/tts" \
    -H "Content-Type: application/json" \
    --data-binary @- \
    -o /dev/null <<JSON
{
  "text": "こんにちは。",
  "text_lang": "ja",
  "ref_audio_path": "$SCRIPT_DIR/reference_voices/平静/ref.wav",
  "prompt_lang": "ja",
  "prompt_text": "ふむ、おぬしが我輩のご主人か?",
  "media_type": "wav",
  "streaming_mode": false
}
JSON
}

if curl -fsS --max-time 2 "http://127.0.0.1:9880/docs" >/dev/null 2>&1; then
  echo "检测到已有 GPT-SoVITS 服务，正在进行语音自检..."
  if tts_smoke_test; then
    tts_ready="true"
  else
    echo "已有 GPT-SoVITS 服务异常，正在请求重启..."
    curl -sS --max-time 5 -X POST "http://127.0.0.1:9880/control" \
      -H "Content-Type: application/json" \
      --data '{"command":"exit"}' >/dev/null 2>&1 || true
    sleep 2
    clear_tts_port
  fi
fi

if [[ "$tts_ready" != "true" ]]; then
  echo "正在启动 GPT-SoVITS 本地语音服务（首次启动可能需要几十秒）..."
  (
    cd "$SCRIPT_DIR/GPT-SoVITS"
    export MPLCONFIGDIR="$SCRIPT_DIR/.gpt-sovits-matplotlib"
    export PYTORCH_ENABLE_MPS_FALLBACK=1
    export PYTHONUNBUFFERED=1
    exec "$SCRIPT_DIR/.gpt-sovits-venv/bin/python" api_v2.py -a 127.0.0.1 -p 9880
  ) &
  tts_pid=$!

  for _ in {1..180}; do
    if curl -fsS --max-time 2 "http://127.0.0.1:9880/docs" >/dev/null 2>&1 && tts_smoke_test; then
      tts_ready="true"
      break
    fi
    if ! kill -0 "$tts_pid" 2>/dev/null; then
      echo "GPT-SoVITS 启动失败，请查看上面的错误信息。"
      exit 1
    fi
    sleep 1
  done
  if [[ "$tts_ready" != "true" ]]; then
    echo "GPT-SoVITS 启动超时，桌宠不会继续启动。"
    kill "$tts_pid" 2>/dev/null || true
    exit 1
  fi
fi

echo "GPT-SoVITS 已就绪。"

cleanup() {
  if [[ -n "$tts_pid" ]] && kill -0 "$tts_pid" 2>/dev/null; then
    kill "$tts_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

exec ".venv/bin/python" main.py
