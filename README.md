# workMate

workMate는 일정·메일·업무·지식베이스를 한곳에서 정리하는 Electron 기반 개인 AI 업무 비서입니다. 개발 환경에서는 React Renderer, Electron 데스크톱 셸, FastAPI Backend가 함께 동작합니다.

## 현재 구현 상태

| 영역 | 상태 | 설명 |
| --- | --- | --- |
| 데스크톱 UI | 구현 | Home, Assistant, 업무, 활동 기록, 설정 화면 |
| 업무 관리 | 구현 | SQLite 기반 업무 생성·완료 처리 |
| LLM Agent | 구현 | Mock 또는 OpenAI Responses API 선택 가능 |
| Tool Calling | 구현 | 일정·업무·메일·지식베이스 Tool 호출과 대화 문맥 유지 |
| Calendar | 구현 | Mock / Google Calendar Provider 전환, OAuth Desktop 연결 |
| 변경 승인 | 구현 | 외부 일정 변경은 Action Card 승인 후에만 실행 |
| Mail | Mock | Bizmeka 실제 API 연동은 후속 작업 |
| WorkMemory | 1단계 | 로컬·SMB 마운트 폴더 문서 수집 및 검색 기반 마련 |

## 빠른 실행

macOS에서는 프로젝트 루트에서 아래 명령 하나로 실행합니다.

```bash
./start-workmate.sh
```
스크립트는 다음을 자동으로 처리합니다.

1. Node.js, npm, Python 가상환경 존재 여부 확인
2. `.env`가 없으면 `.env.example`을 복사해 생성
3. Python·Node 의존성이 없을 때 설치
4. Vite, Electron, FastAPI를 함께 실행

Electron 개발 모드가 FastAPI를 직접 시작하고 종료하므로, 별도로 Backend를 실행할 필요가 없습니다. 종료는 실행 중인 터미널에서 `Ctrl+C`를 누릅니다.

Windows에서는 다음 파일을 실행합니다.

```bat
start-workmate.bat
```

Windows 기본 Python 가상환경 경로는 `D:\python\venv`입니다. 다른 경로를 쓰는 경우 실행 전에 `WORKMATE_PYTHON_ENV` 환경 변수로 지정할 수 있습니다.

## 처음 한 번만 하는 준비

필수 환경은 Node.js 20 이상과 Python 3.10 이상입니다. 프로젝트 루트의 `venv` 가상환경을 사용하거나, 이미 사용하는 가상환경 경로를 지정할 수 있습니다.

```bash
python3 -m venv venv
venv/bin/pip install -r backend/requirements.txt
```

다른 가상환경을 쓰려면 실행 전에 경로를 지정합니다.

```bash
WORKMATE_PYTHON_ENV=/원하는/가상환경 ./start-workmate.sh
```

수동으로 준비하려면 다음을 실행합니다.

```bash
cp .env.example .env
npm install
venv/bin/pip install -r backend/requirements.txt
```

`.env`와 OAuth token은 Git에 포함되지 않습니다. Finder에서 점으로 시작하는 파일을 보려면 `Cmd + Shift + .`를 누릅니다.

## 실행 방법

### 통합 실행

```bash
./start-workmate.sh
```

### 개발 명령 직접 실행

```bash
source venv/bin/activate
npm run dev
```

개발 서버는 다음 주소를 사용합니다.

- Renderer: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8765`

개발 중 Renderer DevTools가 필요하면 `.env`에서 아래 값을 설정한 뒤 다시 실행합니다.

```dotenv
WORKMATE_OPEN_DEVTOOLS=1
```

## 검증 스크립트

전체 테스트·타입 검사·린트·빌드를 한 번에 실행합니다.

```bash
./verify-workmate.sh
```

개별 명령은 아래와 같습니다.

```bash
npm run backend:test
npm run typecheck
npm run lint
npm run build
```

## OpenAI 연결

기본값은 외부 호출이 없는 Mock 모드입니다.

```dotenv
WORKMATE_LLM_PROVIDER=mock
```

실제 OpenAI Responses API를 사용하려면 프로젝트 루트의 `.env`에 다음을 설정하고 앱을 다시 시작합니다.

```dotenv
WORKMATE_LLM_PROVIDER=openai
OPENAI_API_KEY=실제_API_키
WORKMATE_LLM_MODEL=gpt-4.1-mini
```

실행 후 Assistant에 메시지를 보내고 터미널에서 아래 로그를 확인하면 실제 OpenAI Provider가 동작한 것입니다.

```text
agent_request provider=openai
```

일정·메일·업무처럼 Tool이 필요한 요청에서는 선택된 Tool과 성공 여부도 안전한 범위에서 로그에 표시됩니다. API 키, 프롬프트 원문, OAuth token, 메일 본문은 로그에 기록하지 않습니다.

## Google Calendar 연결

기본값은 안전한 Mock Calendar입니다.

```dotenv
WORKMATE_CALENDAR_PROVIDER=mock
WORKMATE_TIMEZONE=Asia/Seoul
GOOGLE_CALENDAR_ID=primary
```

실제 Google Calendar를 연결하려면 다음 순서로 준비합니다.

1. Google Cloud Console에서 **Google Calendar API**를 활성화합니다.
2. OAuth 동의 화면을 구성합니다.
3. 애플리케이션 유형이 **Desktop app**인 OAuth Client를 생성합니다.
4. 받은 Client JSON을 저장소 밖의 안전한 위치에 둡니다.
5. `.env`에 아래 값을 설정합니다.

```dotenv
WORKMATE_CALENDAR_PROVIDER=google
GOOGLE_CALENDAR_CREDENTIALS_PATH=/절대경로/google-oauth-desktop-client.json
GOOGLE_CALENDAR_ID=primary
```

6. 앱을 다시 시작한 뒤 **설정 → Google Calendar → Google 계정 연결**을 선택합니다.

첫 연결 뒤의 refresh token은 `backend/data/google_calendar_token.json`에 사용자 전용 권한으로 저장됩니다. 이 파일은 Git에서 제외됩니다. 연결 해제는 이 기기의 로컬 token만 제거하며 Google 계정의 OAuth 권한 자체를 취소하지는 않습니다.

오늘·내일 일정, 검색, 종일 일정, 반복 일정 occurrence를 지원합니다. 일정 생성·수정·삭제는 Agent가 즉시 실행하지 않고 Action Card를 만든 뒤 사용자의 승인 후에만 반영합니다.

## 지식베이스 — WorkMemory 1단계

로컬 폴더와 macOS에 마운트된 SMB 폴더의 문서를 수집할 수 있습니다. 원본 파일과 기존 WorkMemory/MariaDB 데이터는 변경하지 않습니다.

- 등록 → 백그라운드 수집·파싱 → 문서 목록 → 추출 원문 열람
- 지원 형식: TXT, Markdown, CSV, 코드, JSON/YAML/SQL, PDF, DOCX, XLSX
- `.env`, 인증서·개인키, `node_modules`, 가상환경 폴더는 수집 대상에서 제외
- 파일 최대 25 MiB, PDF 최대 500쪽, XLSX 최대 100,000셀 제한
- SHA-256 기반 변경 감지와 삭제 파일 숨김 처리
- 수집 진행률 표시 및 파일 단위 안전 중단

SMB 주소 자체가 아니라 macOS에 마운트된 폴더 경로를 등록합니다. 일반적으로 `/Volumes/공유이름/...` 형식입니다.

## API 요약

| 메서드 | 주소 | 설명 |
| --- | --- | --- |
| `GET` | `/health` | Backend 상태 확인 |
| `GET` | `/api/v1/dashboard` | Home 화면용 브리핑·일정·메일·업무·Provider 상태 |
| `POST` | `/api/v1/chat` | Agent 응답과 선택적 Action Card 반환 |
| `GET` | `/api/v1/tasks` | 업무 목록 |
| `POST` | `/api/v1/tasks` | 업무 생성 |
| `POST` | `/api/v1/tasks/{id}/complete` | 업무 완료 |
| `GET` | `/api/v1/calendar/status` | Calendar 연결 상태 |
| `POST` | `/api/v1/calendar/connect` | Google OAuth 연결 시작 |
| `POST` | `/api/v1/calendar/verify` | Google Calendar 연결 확인 |
| `POST` | `/api/v1/calendar/disconnect` | 로컬 Calendar token 제거 |
| `POST` | `/api/v1/actions/{id}/approve` | 승인된 Calendar Action 실행 |

## 프로젝트 구조

```text
workMate/
├── electron/                 # Electron main / preload
├── src/                      # React + TypeScript Renderer
├── backend/
│   ├── server/agent/         # Agent와 Tool Calling
│   ├── server/api/           # FastAPI 라우터와 의존성 구성
│   ├── server/providers/     # OpenAI·Google Calendar Provider
│   ├── server/services/      # 업무·대시보드·승인 로직
│   ├── server/tools/         # 일정·메일·업무·메모리 Tool
│   └── tests/                # Backend 테스트
├── .env.example              # 환경변수 템플릿
├── start-workmate.sh         # macOS 실행 스크립트
├── start-workmate.bat        # Windows 실행 스크립트
└── verify-workmate.sh        # 전체 검증 스크립트
```
