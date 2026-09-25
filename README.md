# MONEY ENGINE V3.0 — PHASE 2

Windows PC에서 로컬로 실행하는 콘텐츠 작업 앱입니다. 소재 입력 시 작업을 저장하고 OpenAI 웹 검색으로 공식자료를 찾은 뒤, 직접 읽은 근거로 사실별 VERIFIED/CONFLICT/UNKNOWN을 기록합니다. 본문·태그·이미지는 다음 단계까지 `PENDING`입니다.

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

## PHASE 1 확인

1. CREATE에 소재를 붙여넣고 **분석 및 콘텐츠 생성**을 누릅니다.
2. `입력 저장: 완료`와 Research 단계의 진행 상태를 확인합니다.
3. CONTENT 목록에서 저장된 제목을 눌러 원문과 상태를 다시 엽니다.
4. 서버를 재시작한 뒤에도 목록과 작업이 유지되는지 확인합니다.

## PHASE 2 Research 확인

1. SETTINGS에 **OpenAI API 키**를 저장합니다. ChatGPT 로그인과 별도로 OpenAI API 이용 권한 및 비용이 필요합니다.
2. CREATE에 보은·순천·춘천 공고 소재 중 하나를 붙여넣고 **분석 및 콘텐츠 생성**을 누릅니다.
3. 소재 분석·공식자료·사실 검증의 상태가 진행 중에서 완료/주의로 바뀌는지 확인합니다.
4. Research 결과에서 12개 사실, 공식 URL, 첨부자료 및 출처 날짜를 확인합니다. 직접 읽지 못한 근거는 UNKNOWN입니다.
5. CONTENT에서 다시 열어 결과가 유지되는지 확인합니다. 실패했다면 같은 화면의 **Research 다시 시도**를 누릅니다.

`backend/providers/`에 OpenAI LLM·웹 검색 어댑터를 분리했습니다. 키는 Windows DPAPI로 저장되고 EXE에 포함되지 않습니다. PDF/HWP/HWPX/DOCX/XLSX는 자료 접근과 텍스트 추출이 가능한 경우 분석합니다. 접근 제한·스캔 이미지·읽기 실패 자료는 확인 불가로 처리합니다. 외부 API가 필요한 실제 검색은 Windows에서 API 키를 넣어 검증합니다.
