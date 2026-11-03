#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

DEFAULT_SHARED_ENV="/Users/bizntech/Desktop/python/venv"
PYTHON_ENV="${WORKMATE_PYTHON_ENV:-$DEFAULT_SHARED_ENV}"
PYTHON_BIN="$PYTHON_ENV/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python 가상환경을 찾을 수 없습니다: $PYTHON_ENV"
  echo "WORKMATE_PYTHON_ENV 환경 변수로 가상환경 위치를 지정해 주세요."
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import fastapi, openai, pypdf, docx, openpyxl, googleapiclient, google_auth_oauthlib' >/dev/null 2>&1; then
  echo "백엔드 의존성을 설치합니다..."
  "$PYTHON_BIN" -m pip install -r backend/requirements.txt
fi

echo "백엔드 테스트를 실행합니다..."
(cd backend && "$PYTHON_BIN" -m pytest)

echo "프런트엔드 타입 검사와 린트를 실행합니다..."
npm run typecheck
npm run lint

echo "프로덕션 빌드를 실행합니다..."
npm run build

echo "모든 검증을 통과했습니다."
