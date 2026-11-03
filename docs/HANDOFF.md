# workMate 작업 인수인계 (집에서 이어서 작업하기)

마지막 갱신: 2026-11-03

## 1. 프로젝트 한 줄 요약
Electron + React/Vite + FastAPI + SQLite 기반 개인 AI 업무 비서. 화면 이름은 **메이트**(구 Assistant).
OpenAI Responses API Agent가 Tool Registry로 업무·메모·일정·메일을 다루며, **외부/데이터를 바꾸는 작업은 모두 Action Card 승인 후에만 실행**한다.

## 2. 집에서 시작하기

```bash
git pull                      # 회사에서 push한 뒤 (아래 4번 참고)
npm install
cp .env.example .env          # .env는 git에 없다. 키를 새로 넣어야 한다
```

- 백엔드 Python 환경: `start-workmate.sh` / `verify-workmate.sh`는 `/Users/bizntech/Desktop/python/venv`를 기본 경로로 쓴다.
  집 PC에 없으면 `WORKMATE_PYTHON_ENV=<venv 경로>`로 지정하거나 새 venv를 만들고 `pip install -r backend/requirements.txt`.
- 전체 검증: `./verify-workmate.sh` (pytest + typecheck + lint + build)
- 백엔드 테스트만: `cd backend && <venv>/bin/python -m pytest` (반드시 `backend` 폴더에서 실행)
- 실행: `./start-workmate.sh` 또는 `npm run dev`

### git에 없어서 집에서 다시 해야 하는 것
| 항목 | 설명 |
| --- | --- |
| `.env` | `OPENAI_API_KEY`, `WORKMATE_LLM_PROVIDER`, `WORKMATE_MAIL_PROVIDER` 등. `.env.example` 참고 |
| SQLite DB | `backend/data/workmate.db`는 git 제외. 집에서는 빈 DB로 시작한다 (업무·메모·대화 기록 없음) |
| Google Calendar 연결 | OAuth 클라이언트 JSON(`google.json` 등)과 토큰(OS 키체인)은 PC별. 집에서 설정 → Google 계정 연결을 다시 해야 한다. `docs/google-calendar-setup.md` |
| Bizmeka | 세션 정보는 PC별. `docs/bizmeka-mail-setup.md` |

메일 provider 기본 권장값은 `WORKMATE_MAIL_PROVIDER=none`. `mock`이면 "Mock" 표시가 붙은 샘플 메일이 보인다.

## 3. 구현된 기능 (모두 pytest 107개 통과 기준)

- **업무(Task)**: CRUD, 시작일(`start_at`)·종료일(마감, `due_at`) 선택 입력, 시작일 ≤ 종료일 검증, 기간 표시(`2026.11.05 ~ 2026.11.10`). 마이그레이션은 `start_at` 컬럼 추가 방식(기존 데이터 보존).
- **메모(Memo)**: 독립 메모, 업무 연결 선택.
- **지식베이스**: 레이아웃 개편.
- **메이트(Assistant) Tool Calling**: 업무·메모·일정 생성/수정/완료/삭제는 `propose_*` → 승인 카드 → 승인 후 정확히 1회 실행. 대상이 여럿이면 되묻는다. 말하지 않은 날짜는 만들지 않는다.
- **Google Calendar**: OAuth(루프백+PKCE), 토큰은 OS 키체인(없으면 0600 파일), 일정 CRUD 승인 카드. 반복 일정 변경/삭제는 미지원.
- **대화 기록 영속화**: SQLite, 메뉴 이동·재시작 후 복원, 실행 불가 카드는 만료 처리, 키/토큰은 마스킹.
- **Bizmeka 메일**: Provider `none` / `mock` / `bizmeka`. 메일 원문은 LLM에 보내지 않고(건수·상태만) 화면에만 표시하며 DB·로그·활동기록에 저장하지 않는다. 발송·삭제(휴지통 이동만)·이동·읽음 변경은 승인 카드 필수. 발송 타임아웃은 "확인 필요"로 두고 자동 재발송 없음. `WORKMATE_MAIL_ALLOW_LIVE=1`이 아니면 실서버 요청 자체를 만들지 않는다.
- **UI**: 메뉴 이름 `메이트`, 대화 말투는 개인 비서형 해요체(`backend/server/agent/agent.py`의 `SYSTEM_INSTRUCTIONS` 끝 "Conversation style" 문단).

## 4. 아직 커밋/푸시되지 않은 상태 (중요)
이 작업의 대부분이 **미커밋 상태**다 (수정·신규 파일 다수). 집에서 이어 작업하려면 회사 PC에서 먼저 커밋하고 push해야 한다.

```bash
git status
git add -A && git commit -m "업무 기간, Bizmeka 메일, 대화 기록 등"
git push
```

`.env`, DB, 토큰은 `.gitignore`로 제외되어 있어 같이 올라가지 않는다. (`git status`로 올라갈 파일을 확인한 뒤 커밋할 것)

## 5. 검증된 것 / 안 된 것
- 검증됨: pytest 107개, `npm run build`, `npm run lint`. 브라우저 화면에서 Mock 메일 화면·승인 흐름 확인. 과거에 실제 OpenAI·실제 Google Calendar 시나리오 확인.
- **미검증**: 실제 Electron 창에서의 조작, 실제 Bizmeka 서버, 바뀐 말투·업무 기간 입력의 실제 OpenAI 응답/화면 확인.

## 6. 남은 작업 / 다음 후보
1. 업무 기간·새 말투를 실제 OpenAI + Electron 화면에서 확인
2. Bizmeka 실연동 전 사내 IT 확인 (인증 방식, 경로 오타 `moblieApi`, 요청 형식, 발송 멱등성 등 → `docs/bizmeka-mail-setup.md` 체크리스트) 후 읽기 전용부터 단계적 검증
3. 상주 UI·전역 단축키·OS 알림, 아침 브리핑·선제 알림 (Notion 페이지의 기존 우선순위 2~3번)
4. WorkMemory API 연동
5. 배포용 credential vault, Python runtime 패키징

알려진 한계: 메일 전달 시 원본 첨부 자동 첨부 없음, 예약발송 서버 동작 미확인, 발송 실패 시 업로드된 임시 파일이 서버에 남을 수 있음.

## 7. 참고 문서
- `docs/bizmeka-mail-setup.md`, `docs/google-calendar-setup.md`, `README.md`
- Notion: "workMate 개발 현황 및 다음 작업" 페이지 (전체 로드맵·히스토리)

## 8. 작업 규칙 (이 프로젝트에서 지켜온 것)
- 기존 DB 데이터는 삭제·초기화하지 않는다. 마이그레이션은 컬럼 추가 방식으로.
- 승인 전에는 DB·외부 서비스를 바꾸지 않는다. 키/토큰/메일 원문은 코드·로그·대화 DB에 남기지 않는다.
- 보고할 때 "테스트 통과"와 "실제 앱에서 확인"을 구분한다.
