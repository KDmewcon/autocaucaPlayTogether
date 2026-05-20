#!/usr/bin/env bash
# Chạy AutoClicker app.
# Lần đầu sẽ tự tạo venv với Python 3.12 và cài deps.
#
# Usage:
#   ./run.sh           - chạy app
#   ./run.sh setup     - chỉ cài deps, không chạy
#   ./run.sh clean     - xóa venv, dọn cache

set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-}"

# Tìm Python 3.12+ ưu tiên Homebrew (pyobjc 12 cần macOS recent)
find_python() {
  if [ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ]; then
    echo "$PYTHON_BIN"
    return
  fi
  for cand in \
    /opt/homebrew/bin/python3.13 \
    /opt/homebrew/bin/python3.12 \
    /usr/local/bin/python3.13 \
    /usr/local/bin/python3.12 \
    "$(command -v python3.13 || true)" \
    "$(command -v python3.12 || true)" \
    "$(command -v python3 || true)"; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then
      echo "$cand"
      return
    fi
  done
}

PY="$(find_python)"
if [ -z "$PY" ]; then
  echo "[!] Không tìm thấy Python 3.12+. Cài bằng:  brew install python@3.12"
  exit 1
fi

ensure_venv() {
  if [ ! -d ".venv" ]; then
    echo "[*] Tạo venv với $PY ..."
    "$PY" -m venv .venv
    .venv/bin/pip install --upgrade pip > /dev/null
    echo "[*] Cài requirements..."
    .venv/bin/pip install -r requirements.txt
    # Optional audio deps (sounddevice cần PortAudio - brew install portaudio)
    .venv/bin/pip install sounddevice soundfile audioread || \
      echo "[!] Không cài được sounddevice/soundfile. Audio steps sẽ không khả dụng."
    echo "[✓] Setup xong."
  fi
}

case "${1:-run}" in
  setup)
    ensure_venv
    ;;
  clean)
    echo "[*] Xóa .venv và cache..."
    rm -rf .venv
    find . -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
    echo "[✓] Done."
    ;;
  run|"")
    ensure_venv
    exec .venv/bin/python run.py
    ;;
  *)
    echo "Usage: $0 [run|setup|clean]"
    exit 1
    ;;
esac
