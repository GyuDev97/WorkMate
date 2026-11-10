@echo off
setlocal EnableExtensions
rem Keep Korean status messages readable in modern Windows terminals.
chcp 65001 >nul
cd /d "%~dp0"

where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo Node.js와 npm을 설치한 뒤 다시 실행해 주세요.
  pause
  exit /b 1
)

rem Prefer an explicitly configured venv, then project-local environments.
if not "%WORKMATE_PYTHON_ENV%"=="" goto :python_env_selected
if exist "%~dp0.venv\Scripts\python.exe" set "WORKMATE_PYTHON_ENV=%~dp0.venv"
if not "%WORKMATE_PYTHON_ENV%"=="" goto :python_env_selected
if exist "%~dp0venv\Scripts\python.exe" set "WORKMATE_PYTHON_ENV=%~dp0venv"
if not "%WORKMATE_PYTHON_ENV%"=="" goto :python_env_selected

rem Create a self-contained project environment from Python 3.15 when needed.
for /f "delims=" %%P in ('where python.exe 2^>nul') do (
  "%%P" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 15) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "BOOTSTRAP_PYTHON=%%P"
    goto :create_project_venv
  )
)
echo Python 3.15를 찾을 수 없습니다. Python 3.15를 설치한 뒤 다시 실행해 주세요.
goto :error

:create_project_venv
if not "%BOOTSTRAP_PYTHON%"=="" (
  echo 프로젝트 Python 가상환경을 생성합니다...
  "%BOOTSTRAP_PYTHON%" -m venv "%~dp0.venv"
  if errorlevel 1 goto :error
  set "WORKMATE_PYTHON_ENV=%~dp0.venv"
  goto :python_env_selected
)

:python_env_selected
set "PYTHON_BIN=%WORKMATE_PYTHON_ENV%\Scripts\python.exe"
if not exist "%PYTHON_BIN%" (
  echo Python 가상환경을 찾을 수 없습니다: %WORKMATE_PYTHON_ENV%
  echo .venv 또는 venv 폴더를 만들거나, WORKMATE_PYTHON_ENV 환경 변수로 위치를 지정해 주세요.
  pause
  exit /b 1
)
"%PYTHON_BIN%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 15) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.15 가상환경이 필요합니다: %WORKMATE_PYTHON_ENV%
  goto :error
)

if not exist ".env" (
  echo .env를 생성합니다. OpenAI 또는 Google 설정은 나중에 .env에서 입력할 수 있습니다.
  copy /Y ".env.example" ".env" >nul
)

"%PYTHON_BIN%" -c "import fastapi, openai, pypdf, docx, openpyxl, googleapiclient, google_auth_oauthlib, tzdata; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Seoul')" >nul 2>nul
if errorlevel 1 (
  echo 백엔드 의존성을 설치합니다...
  "%PYTHON_BIN%" -m pip install -r backend\requirements.txt
  if errorlevel 1 goto :error
)

if not exist "node_modules" (
  echo 프런트엔드 의존성을 설치합니다...
  call npm install
  if errorlevel 1 goto :error
)

echo workMate 데스크톱 앱을 시작합니다...
echo 종료하려면 실행 중인 터미널에서 Ctrl+C를 누르세요.
set "WORKMATE_PYTHON=%PYTHON_BIN%"
call npm run dev
if errorlevel 1 goto :error
goto :end

:error
echo 실행 준비 중 오류가 발생했습니다.
pause

:end
endlocal
