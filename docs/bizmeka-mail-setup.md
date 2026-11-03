# Bizmeka 메일 연동 설정

WorkMate의 메일 기능은 **메일 원본과 LLM을 분리**합니다. 메일 본문·제목·주소·첨부는 FastAPI 백엔드와 화면 사이로만 오가고, OpenAI에는 건수와 성공/실패 같은 상태만 전달됩니다.

## 1. Provider 선택
`.env`의 `WORKMATE_MAIL_PROVIDER`로 고릅니다.

| 값 | 동작 |
|---|---|
| `none` (기본) | 메일 미연결. 화면에 "미연결" 안내만 표시합니다. |
| `mock` | 개발용 고정 샘플(`.invalid` 주소). 화면 곳곳에 **Mock 데이터** 표시가 붙습니다. 실제 회사 메일이 아닙니다. |
| `bizmeka` | 실제 Bizmeka API 어댑터. 아래 조건이 모두 충족돼야만 호출합니다. |

## 2. Bizmeka 환경변수
```
WORKMATE_MAIL_PROVIDER=bizmeka
BIZMEKA_BASE_URL=https://<사내 Bizmeka 서버>      # 슬래시 없이
BIZMEKA_TENANT_ID=<회사 ID>
BIZMEKA_PORTAL_ID=<포탈 ID>
BIZMEKA_USER_ID=<사용자 ID>
BIZMEKA_MAIL_USER_ID=<사용자 메일 주소>
BIZMEKA_MAC_ADDRESS=<명세의 macAddress 값>
BIZMEKA_FROM_NAME=<보내는 사람 이름>
BIZMEKA_REQUEST_ENCODING=form                    # form | json (명세에 형식이 없어 설정 가능)
WORKMATE_MAIL_ALLOW_LIVE=0                       # 사내 IT 검증이 끝나기 전에는 0 유지
# 선택
BIZMEKA_TRASH_FOLDER_KEY=                        # 휴지통 폴더키를 자동으로 찾지 못할 때
BIZMEKA_MAX_DOWNLOAD_MB=25
BIZMEKA_READ_TIMEOUT=20
BIZMEKA_SEND_TIMEOUT=60
# 경로 오버라이드(명세의 moblieApi 오타 확인 후)
# BIZMEKA_PATH_DOWNLOAD=/mobileApi/downloadAttach.json
# BIZMEKA_PATH_WRITE=/mobileApi/write.json
```
`WORKMATE_MAIL_ALLOW_LIVE=1`이 아니면 **어떤 HTTP 요청도 만들어지지 않습니다**(운영 서버 보호용 안전장치).

### 세션 정보(인증)
명세서에는 로그인·토큰 발급 API가 없습니다(SSO는 SAML 웹 연동). 인증 방식이 확인되기 전까지 임의로 만들지 않았고, 확인된 세션 값을 **환경변수 `BIZMEKA_SESSION_COOKIE`(Cookie 헤더 전체 값) 또는 OS 보안 저장소**에 넣는 경계만 제공합니다.
```
python -c "import keyring; keyring.set_password('workMate','bizmeka-session','<Cookie 헤더 값>')"
```
세션 정보는 코드, 로그, Tool trace, 대화 DB, 활동 기록에 기록되지 않습니다. 세션이 만료(`returnCode=3`)되면 "재인증 필요"로 표시됩니다.

## 3. 승인과 안전 규칙
- 발송, 임시보관 저장, 삭제(휴지통 이동), 이동, 읽음/안읽음 변경은 모두 **확인 카드 승인 후에만** 서버에 요청합니다. 승인 전에는 서버를 호출하지 않습니다.
- 삭제는 휴지통 이동만 지원합니다. 완전 삭제와 메일함 비우기는 구현하지 않았습니다.
- 발송 요청 뒤 응답을 받지 못하면 **확인 필요** 상태로 두고 자동 재발송하지 않습니다. 보낸편지함에서 확인한 뒤 필요하면 새로 요청하세요.
- 첨부파일은 로컬 임시 폴더(`backend/data/mail_staging`)에만 보관하고, 발송 승인 시에만 업로드합니다. 24시간이 지난 파일은 정리됩니다.

## 4. 실행
```
./start-workmate.sh
```
메일 화면은 사이드바 **메일**입니다. Assistant에서는 미확인 건수, 검색(목록은 화면에만 표시), 새 메일 작성 요청이 가능하고, 답장·전달·삭제·이동·읽음 변경은 메일 화면에서 합니다.

## 5. 실제 연동 전 사내 IT/Bizmeka에 확인할 사항
1. **인증:** 요청에 어떤 세션/토큰을 어떻게 실어야 하는지(쿠키·헤더), 만료 시간, 재인증 절차, `macAddress` 검증 여부와 값의 출처.
2. **경로:** `/moblieApi/downloadAttach.json`, `/moblieApi/write.json`의 `moblie` 오타 여부와 `mobileApi/list.json`(슬래시 없음)의 실제 경로.
3. **요청 형식:** form-urlencoded인지 JSON인지, `Dmail[]` 배열의 직렬화 방식.
4. **응답 구조:** `header.returnCode`/`body` 중첩 여부, `returnCode`의 전체 코드표(0·3·4 외).
5. **메일 수:** `mailCount.json`이 두 인터페이스(미확인/전체)로 중복 기재된 이유와 `folder`, `folderBounds` 값의 의미.
6. **목록/검색:** `folderId`와 `folder` 중 필수 항목, `page`/`total`의 의미, `isNewmail` 동작, 보낸편지함의 수신자 필드.
7. **첨부 다운로드:** 응답이 바이너리 스트림인지 JSON인지, 발송용 필드(`emailAddress` 등 비필수 여부)를 모두 보내야 하는지.
8. **삭제:** `delete.json`이 휴지통 이동인지 영구 삭제인지. WorkMate는 `move.json`으로 휴지통(`Trash_*`)에 이동하는 방식만 사용합니다. 휴지통 폴더키 규칙.
9. **발송:** `write.json`→`uploadFile.json`→`send.json` 순서와 `tempKey` 필수 여부, `attachments`의 `<filekey>:0:1` 형식 의미, `first` 값이 `send.json`(0/1/2)과 `write.json`(1/2/3/4)에서 다른 이유, **중복 발송 방지(멱등성) 지원 여부**, 응답 지연 시 발송 여부를 확인하는 방법, `reserverTime` 예약발송 지원.
10. **업로드:** multipart 필드명 `file`, 파일 크기·형식 제한, 승인 후 발송 실패 시 서버에 남는 임시 업로드의 정리 방식.
11. **운영 정책:** 호출 빈도 제한, IP 허용 목록(사내망/VPN), 감사 로그, 개인정보·보안 검토, 본문 HTML 표시 정책.
