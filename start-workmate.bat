@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python 3를 설치한 뒤 다시 실행해 주세요.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js와 npm을 설치한 뒤 다시 실행해 주세요.
  pause
  exit /b 1
)

if "%WORKMATE_PYTHON_ENV%"=="" set "WORKMATE_PYTHON_ENV=D:\python\venv"
set "PYTHON_BIN=%WORKMATE_PYTHON_ENV%\Scripts\python.exe"
if not exist "%PYTHON_BIN%" (
  echo Python 가상환경을 찾을 수 없습니다: %WORKMATE_PYTHON_ENV%
  echo WORKMATE_PYTHON_ENV 환경 변수로 가상환경 위치를 지정해 주세요.
  pause
  exit /b 1
)

if not exist ".env" (
  echo .env를 생성합니다. OpenAI 또는 Google 설정은 나중에 .env에서 입력할 수 있습니다.
  copy /Y ".env.example" ".env" >nul
)

"%PYTHON_BIN%" -c "import fastapi, openai, pypdf, docx, openpyxl, googleapiclient, google_auth_oauthlib" >nul 2>nul
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
goto :end

:error
echo 실행 준비 중 오류가 발생했습니다.
pause

:end
endlocal
