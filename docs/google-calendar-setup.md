# Google Calendar 연동 설정

WorkMate는 데스크톱 앱용 OAuth 2.0(Authorization Code + PKCE, 로컬 루프백 리디렉션)으로 Google Calendar에 접근합니다.
요청하는 권한은 일정 읽기·쓰기에 필요한 `https://www.googleapis.com/auth/calendar.events` 하나뿐입니다.

## 1. Google Cloud Console에서 직접 해야 할 일 (한 번만)
1. [console.cloud.google.com](https://console.cloud.google.com)에서 프로젝트를 만들거나 선택합니다.
2. **API 및 서비스 → 라이브러리**에서 **Google Calendar API**를 *사용 설정*합니다.
3. **API 및 서비스 → OAuth 동의 화면**을 설정합니다.
   - 사용자 유형: 개인 Gmail이면 *외부*, Workspace 조직이면 *내부*도 가능합니다.
   - 범위(Scopes)에 `.../auth/calendar.events`를 추가합니다.
   - 게시 상태가 *테스트*이면 **테스트 사용자**에 본인 Google 계정을 추가해야 합니다. (테스트 상태의 토큰은 7일 후 만료되어 다시 연결해야 합니다. 계속 쓰려면 *프로덕션으로 게시*하세요.)
4. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID**를 만들고 애플리케이션 유형을 **데스크톱 앱**으로 선택합니다.
5. 생성 후 **JSON 다운로드**를 누르고, 이 파일을 **저장소 밖**(예: `~/.config/workmate/google-oauth-client.json`)에 보관합니다. 이 파일은 커밋하지 마세요.

## 2. WorkMate 설정
`.env`에 다음을 설정하고 백엔드를 재시작합니다.

```
WORKMATE_CALENDAR_PROVIDER=google
GOOGLE_CALENDAR_CREDENTIALS_PATH=/absolute/path/to/google-oauth-client.json
GOOGLE_CALENDAR_ID=primary
# 선택: auto(기본, OS 보안 저장소 우선) | keyring | file
# WORKMATE_TOKEN_STORE=auto
```

앱의 **설정 → Google Calendar → Google 계정 연결**을 누르면 시스템 브라우저가 열립니다. 로그인하고 권한을 허용하면 WorkMate로 돌아옵니다(3분 안에 완료해야 합니다).

## 3. 토큰 저장과 보안
- 토큰은 기본적으로 OS 보안 저장소(macOS Keychain 등)에 저장됩니다. 사용할 수 없으면 `data/google_calendar_token.json`(권한 0600)으로 대체합니다.
- 토큰과 OAuth 클라이언트 정보는 프론트엔드·로그에 출력되지 않습니다.
- **연결 해제**는 Google에 권한 취소를 요청(실패해도 계속 진행)하고 로컬 토큰을 삭제합니다.
- 일정 변경은 항상 Action Card 승인 후에만 실행되며, 참석자에게 알림 메일을 보내지 않습니다(`sendUpdates=none`).

## 4. 문제 해결
| 증상 | 원인 / 조치 |
|---|---|
| `access_denied` / "앱이 확인되지 않음" | 동의 화면이 *테스트*이면 테스트 사용자에 본인 계정을 추가하세요. |
| 403 접근 거부 | Calendar API가 사용 설정되지 않았거나 범위가 빠졌습니다. |
| 며칠 뒤 "연결이 만료되었습니다" | 테스트 게시 상태의 7일 토큰 만료입니다. 다시 연결하거나 프로덕션으로 게시하세요. |
| 브라우저가 열리지 않음 | 3분 안에 완료하지 않으면 시간이 초과됩니다. 다시 연결을 누르세요. |
