# MONEY ENGINE V3.0

Windows PC에서 로컬로 실행하는 콘텐츠 작업 앱입니다. 소재 하나로 공식자료 조사, 사실 검증, 검색수요·의도 분석, 글·태그 생성, 실제 이미지 3장 및 발행 패키지까지 진행합니다. 공식 근거가 부족하면 `REVIEW_REQUIRED`로 표시합니다.

## 품질 차단과 재조사

Research는 입력의 지역·기관·사업명·고유 표현을 검색 anchor로 유지합니다. 첫 조사에서 공식 근거가 없거나 핵심 사실이 부족하면 검색어를 바꿔 공식 공고·보도자료·첨부파일을 제한 횟수 내에 다시 찾습니다. 공식 출처와 핵심 사실 검증률(70% 이상)이 확보되지 않거나 출처가 충돌하면 `CONTENT_BLOCKED / REVIEW_REQUIRED`로 표시하고 본문·태그·이미지 API를 호출하지 않습니다. **Research 다시 시도**로 해당 조사 단계부터 실행할 수 있습니다.

Research V2는 정확한 소재 → 명칭 변형 → 뉴스 발견 → 공식 도메인 → 공고·첨부자료 순서로 최대 5회 검색합니다. 입력의 숫자와 연도는 검색 힌트로만 사용하며 검증된 사실로 간주하지 않습니다. 관련 없는 자료는 REJECTED로 저장해 출처 목록에서 숨기고, 뉴스는 공식 원문을 찾는 DISCOVERY 출처로 사용합니다. **검색 진단**에서 검색어·채택/제외 수·제외 이유·공식 도메인과 첨부 탐색 여부를 볼 수 있습니다. 재시도는 이전 검색어와 미검증 필드를 참조합니다.

Research V2.1은 OpenAI 웹 검색의 URL 전용 source와 제목이 있는 citation을 합치고, 제목이나 요약이 비어 있는 공식 결과는 원문을 읽은 뒤 관련성을 판정합니다. 검색어는 지역·연도·분기·사업명 중복을 제거하고, 결과가 0개면 지역과 핵심 주제를 유지한 채 조건을 줄여 재검색합니다. 진단에서는 검색 공급자, 실제 검색어, 출처별 fetch/parse 상태, 사실 추출 실행 여부와 검증된 fact 수를 표시합니다. 공식 페이지를 찾았어도 읽기 또는 본문 추출이 실패하면 별도 상태로 표시합니다.

본문 생성 전 각 H2에 VERIFIED 사실과 출처가 있는 ARTICLE PLAN을 저장합니다. 생성된 본문에서 근거 없는 숫자와 사실 없는 H2, 반복되는 UNKNOWN 일반론을 검사합니다. `QUALITY_FAIL`이면 본문을 발행 결과로 보여주지 않고 이미지 생성을 중단합니다.

이미지에는 본문 섹션과 연결된 장면 계획을 저장합니다. 이미지 API 결과는 시각 QA로 글자·왜곡·광고처럼 보이는 장면·주제 이탈을 검사하고, 한 장만 최대 2회 생성합니다. QA가 확인되지 않으면 이미지를 통과시키지 않습니다. 이 과정에는 이미지 생성 및 이미지 분석 API 비용이 발생할 수 있습니다.

## 배포판 사용 (일반 사용자)

GitHub Actions의 `Build Money Engine for Windows` 작업에서 `MoneyEngine-Windows` Artifact를 내려받습니다. Artifact의 압축을 풀면 나오는 `MoneyEngine-Windows.zip`도 풀고, 그 안의 `MoneyEngine-Windows/MoneyEngine.exe`를 더블클릭합니다. 기본 브라우저가 자동으로 열립니다. Python, Node.js, pip, 터미널 입력이 필요하지 않습니다. 서버가 이미 실행 중이면 새 서버를 만들지 않고 기존 주소를 엽니다. 종료는 SETTINGS의 **프로그램 종료**를 누릅니다.

SQLite DB, 이미지 폴더, 암호화된 API 키는 EXE 옆의 `data/`, 실행 로그는 `logs/`에 남습니다. 업데이트할 때 `data/`를 보관하고 새 배포 폴더로 복사하면 기존 데이터가 유지됩니다. API 키는 SETTINGS에서 입력하고 Windows DPAPI로 현재 사용자 계정에 연결해 암호화합니다.

`.github/workflows/build-windows.yml`은 GitHub의 `windows-latest`에서 Python 3.12와 Node.js 22를 설치하고, `packaging/build-windows.ps1`로 프런트엔드·Python 패키지·PyInstaller 빌드와 EXE 실물 검증을 수행합니다. ZIP은 `MoneyEngine-Windows/` 아래에 EXE, `_internal/`, `data/`, `logs/`, `사용방법.txt`를 담습니다. GitHub Actions Artifact로 받을 수 있습니다.

## 개발 실행 (개발자 전용)

Python 3.11 이상이 필요합니다. 화면 소스를 다시 빌드할 때만 Node.js 20 이상이 필요합니다. 프로젝트 폴더에서 터미널 두 개를 엽니다.

터미널 1 (Windows PowerShell):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

터미널 2:

```powershell
cd frontend
npm install
npm run dev
```

브라우저에서 `http://127.0.0.1:5173`을 엽니다. 저장 데이터는 `data/money_engine.sqlite3`에 남습니다. 화면을 수정한 뒤 배포할 때는 `frontend`에서 `npm run build`를 실행합니다.

## 사용

1. CREATE에 소재를 붙여넣고 **분석 및 콘텐츠 생성**을 누릅니다.
2. 단계별 진행 상태를 확인합니다. API 키가 없으면 SETTINGS에 키를 저장하고 **실패 단계부터 다시 시도**를 누릅니다.
3. 제목·Meta·본문·태그를 복사하고 이미지별 다운로드 또는 다시 생성을 사용합니다. 일부 텍스트도 개별 수정할 수 있습니다.
4. CONTENT 목록에서 저장된 제목을 눌러 결과를 다시 엽니다.
5. 서버를 재시작한 뒤에도 목록과 작업이 유지되는지 확인합니다.

## 검증

1. SETTINGS에 **OpenAI API 키**를 저장합니다. ChatGPT 로그인과 별도로 OpenAI API 이용 권한 및 비용이 필요합니다.
2. CREATE에 보은·순천·춘천 공고 소재 중 하나를 붙여넣고 **분석 및 콘텐츠 생성**을 누릅니다.
3. 소재 분석·공식자료·사실 검증의 상태가 진행 중에서 완료/주의로 바뀌는지 확인합니다.
4. Research 결과에서 구조화된 사실, 공식 URL, 첨부자료, 출처 날짜와 검색 진단을 확인합니다. 직접 읽지 못한 근거는 UNKNOWN입니다.
5. CONTENT에서 다시 열어 결과와 실제 이미지가 유지되는지 확인합니다. 실패 단계와 실패 이미지 한 장을 개별 재시도할 수 있습니다.

`backend/providers/`에 LLM·웹 검색·이미지 어댑터를 분리했습니다. 키는 Windows DPAPI로 저장되고 EXE에 포함되지 않습니다. PDF/HWP/HWPX/DOCX/XLSX는 자료 접근과 텍스트 추출이 가능한 경우 분석합니다. 접근 제한·스캔 이미지·읽기 실패 자료는 확인 불가로 처리합니다. 웹 검색 결과는 Google 순위나 정확한 월 검색량으로 표시하지 않습니다. 외부 API가 필요한 실제 검색·이미지 생성은 Windows에서 API 키를 넣어 검증해야 합니다.

개발 검증: `python -m unittest discover -s tests -v`, `cd frontend && npm run build`. GitHub Actions는 같은 테스트 통과 후 EXE를 빌드하고 압축된 실행 파일을 smoke test합니다.
