#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3를 설치한 뒤 다시 실행해 주세요."
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js와 npm을 설치한 뒤 다시 실행해 주세요."
  exit 1
fi

DEFAULT_SHARED_ENV="/Users/bizntech/Desktop/python/venv"
if [[ -n "${WORKMATE_PYTHON_ENV:-}" ]]; then
  PYTHON_ENV="$WORKMATE_PYTHON_ENV"
elif [[ -x "$DEFAULT_SHARED_ENV/bin/python" ]]; then
  PYTHON_ENV="$DEFAULT_SHARED_ENV"
elif [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
  PYTHON_ENV="$ROOT_DIR/venv"
else
  PYTHON_ENV="$DEFAULT_SHARED_ENV"
fi
PYTHON_BIN="$PYTHON_ENV/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python 가상환경을 찾을 수 없습니다: $PYTHON_ENV"
  echo "WORKMATE_PYTHON_ENV 환경 변수로 가상환경 위치를 지정해 주세요."
  exit 1
fi

if [[ ! -f .env ]]; then
  echo ".env를 생성합니다. OpenAI 또는 Google 설정은 나중에 .env에서 입력할 수 있습니다."
  cp .env.example .env
fi

if ! "$PYTHON_BIN" -c 'import fastapi, openai, pypdf, docx, openpyxl, googleapiclient, google_auth_oauthlib' >/dev/null 2>&1; then
  echo "백엔드 의존성을 설치합니다..."
  "$PYTHON_BIN" -m pip install -r backend/requirements.txt
fi

if [[ ! -d node_modules ]]; then
  echo "프런트엔드 의존성을 설치합니다..."
  npm install
fi

echo "workMate 데스크톱 앱을 시작합니다..."
echo "종료하려면 실행 중인 터미널에서 Ctrl+C를 누르세요."
# Electron 개발 모드가 이 Python으로 FastAPI를 함께 시작하고 종료합니다.
WORKMATE_PYTHON="$PYTHON_BIN" npm run dev
