# 📊 Market Flow Intelligence System

AI·반도체·HBM·광통신·전력 인프라·우주항공 중심의 **개인 맞춤형 시장 브리핑 시스템**

Claude AI가 글로벌 시장 → 국가 흐름 → 섹터/테마 → 밸류체인 → 관심종목 순서로 분석하여
매일 아침/저녁 리포트를 자동 생성합니다. 지지/저항·손익비, 수급 동향, DART 공시,
등급 적중률 자기검증, 포트폴리오 집중도 진단, 캔들차트 이미지까지 함께 제공합니다.

---

## ⚡ 빠른 시작

```bash
# 1. 의존 패키지 설치 (로컬 개발/대시보드 포함 전체 설치)
pip install -r requirements.txt
# GitHub Actions 등 클라우드 실행만 필요하면 최소 설치:
# pip install -r requirements-cloud.txt

# 2. .env 파일 설정 (아래 환경변수 섹션 참고)

# 3. 헬스체크 — 설정 이상 없는지 먼저 확인
python app/healthcheck.py

# 4. 아침 브리핑 실행
python app/main.py --report morning

# 5. 대시보드 실행
streamlit run app/dashboard.py
```

---

## 🗂️ 프로젝트 구조

```
market_flow/
├── .github/workflows/
│   └── market-flow.yml       # GitHub Actions 자동 실행 (매일 07:00 / 18:30 KST)
├── app/
│   ├── main.py               # CLI 진입점 / 파이프라인 오케스트레이션
│   ├── dashboard.py          # Streamlit 대시보드
│   ├── scheduler.py          # 로컬 자동 스케줄러 (매일 07:00 / 18:30)
│   ├── healthcheck.py        # 시스템 상태 점검
│   ├── collectors/
│   │   ├── price_collector.py       # 주가·기술적지표·지지저항·캔들패턴·수급 수집
│   │   ├── news_collector.py        # 뉴스 수집 (네이버 API / yfinance / Mock)
│   │   ├── macro_collector.py       # 거시지표 수집 (yfinance / Mock)
│   │   └── disclosure_collector.py  # DART 공시 수집 (선택, DART_API_KEY 필요)
│   ├── engine/
│   │   ├── signal_scorer.py       # 7차원 신호 점수 계산
│   │   ├── rating_analyzer.py     # 등급 산정 (추천/안전/보통/주의/위험/판단보류)
│   │   ├── history_tracker.py     # 등급 이력 저장·변화 추적·적중률 자기검증
│   │   └── portfolio_analyzer.py  # 테마/섹터 집중도·당일 동조화 진단
│   ├── reports/
│   │   ├── report_builder.py      # Claude API 리포트 생성
│   │   └── chart_generator.py     # 캔들차트 이미지 생성 (주목 종목 이메일 첨부용)
│   ├── delivery/
│   │   └── email_sender.py        # 이메일 발송 (Gmail SMTP, 캔들차트 인라인 첨부)
│   └── utils/
│       ├── config_loader.py       # 설정 파일 로더
│       ├── data_validator.py      # 데이터 품질 검증 (판단보류·치명적 오류 방어)
│       └── telegram_notifier.py   # 텔레그램 알림 (등급 변화·품질 경고·스크래핑 실패)
├── config/
│   ├── watchlist.json        # 관심종목 목록 (18개)
│   ├── themes.json           # 관심테마 정의
│   ├── user_profile.json     # 투자성향 & 가중치
│   └── report_config.json    # 리포트 설정 (max_tokens 등 일부 필드만 실제 반영)
├── data/
│   ├── reports/               # 생성된 리포트 (.md, .json)
│   ├── history/                # 등급 이력 (ratings_history.json)
│   ├── logs/                   # 실행 로그 (market_flow.log)
│   └── cache/                  # 캐시 데이터
├── .env                      # 환경변수 (API 키 등)
├── 아침 리포트 실행.bat
├── 아침 리포트+이메일.bat
├── 저녁 리포트 실행.bat
├── 저녁 리포트+이메일.bat
├── 대시보드 실행.bat
├── 헬스체크.bat
└── 스케줄러 실행.bat
```

---

## 🔧 환경변수 설정 (.env)

프로젝트 루트에 `.env` 파일을 만들고 아래 내용을 입력합니다.

```env
# ── 필수 ──────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-api03-...    # Claude API 키 (필수)
CLAUDE_MODEL=claude-sonnet-4-6        # 사용할 Claude 모델

# ── 데이터 모드 ────────────────────────────────────
USE_MOCK_DATA=false    # true: Mock 데이터 | false: 실제 yfinance 데이터

# ── 이메일 발송 (선택) ─────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com          # Gmail 주소
SMTP_PASSWORD=xxxx xxxx xxxx xxxx # Gmail 앱 비밀번호 (16자리)
EMAIL_FROM=your@gmail.com
EMAIL_TO=your@gmail.com

# ── 국내 종목 뉴스 (선택) ───────────────────────────
NAVER_CLIENT_ID=...              # 네이버 개발자센터에서 발급
NAVER_CLIENT_SECRET=...          # 미설정 시 국내 종목 뉴스는 테스트 데이터로 대체

# ── DART 공시 연동 (선택) ───────────────────────────
DART_API_KEY=...                 # opendart.fss.or.kr 무료 발급, 미설정 시 공시 데이터 미연동

# ── 텔레그램 알림 (선택) ────────────────────────────
TELEGRAM_BOT_TOKEN=...           # @BotFather에서 /newbot으로 발급
TELEGRAM_CHAT_ID=...             # 봇과 대화 후 getUpdates로 확인

# ── 저장 경로 ──────────────────────────────────────
REPORT_SAVE_DIR=data/reports
REPORT_LANGUAGE=ko
```

> **Gmail 앱 비밀번호 발급**: Gmail → 구글 계정 → 보안 → 2단계 인증 활성화 → 앱 비밀번호 생성
>
> 수급(외국인/기관 순매매) 데이터는 네이버 금융 페이지를 직접 파싱하는 방식이라 별도 키가
> 필요 없으나, 비공식 소스라 페이지 구조가 바뀌면 실패율이 높아질 수 있습니다 — 실패율
> 70% 이상 시 텔레그램으로 자동 경고됩니다(설정된 경우).

---

## 🚀 실행 방법

### 방법 1 — 배치파일 더블클릭 (Windows, 권장)

| 파일 | 설명 |
|------|------|
| `아침 리포트 실행.bat` | 아침 브리핑 생성 |
| `아침 리포트+이메일.bat` | 아침 브리핑 + 이메일 발송 |
| `저녁 리포트 실행.bat` | 저녁 결산 생성 |
| `저녁 리포트+이메일.bat` | 저녁 결산 + 이메일 발송 |
| `대시보드 실행.bat` | Streamlit 대시보드 실행 |
| `스케줄러 실행.bat` | 자동 스케줄러 실행 (07:00 / 18:30) |

### 방법 2 — 명령어 (터미널)

```bash
# 아침 브리핑
python app/main.py --report morning

# 아침 브리핑 + 이메일 발송
python app/main.py --report morning --send-email

# 저녁 결산
python app/main.py --report evening

# 저녁 결산 + 이메일 발송
python app/main.py --report evening --send-email

# 대시보드
streamlit run app/dashboard.py

# 자동 스케줄러 (Ctrl+C로 종료)
python app/scheduler.py

# 헬스체크
python app/healthcheck.py
```

### 방법 3 — Streamlit 대시보드

```bash
streamlit run app/dashboard.py
```

브라우저에서 `http://localhost:8501` 접속 → 사이드바에서 리포트 유형 선택 → **지금 리포트 생성** 클릭

### 방법 4 — GitHub Actions 자동 실행 (운영 권장)

`.github/workflows/market-flow.yml`이 매일 **07:00 / 18:30 KST**에 자동으로 아침/저녁
리포트를 생성해 이메일로 발송합니다. 로컬 PC를 켜둘 필요가 없습니다.

**설정 방법**:
1. 저장소를 GitHub에 push (`.env`는 `.gitignore`에 포함되어 있어 올라가지 않습니다)
2. GitHub 저장소 → **Settings → Secrets and variables → Actions**에서 아래 값을 등록:
   `ANTHROPIC_API_KEY`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`
   (선택: `CLAUDE_MODEL`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `DART_API_KEY`)
3. **Actions** 탭 → **Market Flow Auto Report** → **Run workflow**로 수동 테스트 가능
4. 이후 매일 자동 실행 — 실행 로그는 Actions 탭에서 확인

> 워크플로가 처음에는 "This workflow does not exist"로 안 보일 수 있습니다 — GitHub 인덱싱
> 지연 문제로, 아무 내용이나 한 줄 바꿔서 재커밋·재push하면 즉시 인식됩니다.

---

## 📈 분석 파이프라인

```
[1] 설정 로드 (watchlist, themes, user_profile)
      ↓
[2] 데이터 수집
    - PriceCollector      → yfinance 실시간 주가/거래량/기술적지표/지지저항/
                             캔들패턴/수급(외국인·기관, KR 종목만 네이버 스크래핑)
    - NewsCollector       → 네이버 API(KR) / yfinance(해외) 뉴스, 파생상품 이슈 분리
    - MacroCollector      → S&P500 / KOSPI / VIX / 환율 / 금리 / 원자재
    - DisclosureCollector → DART 공시 (선택, DART_API_KEY 필요)
      ↓
[2-1] 데이터 품질 검증 (DataValidator)
    KOSPI·KODEX200·대형주 교차검증으로 외부 API 데이터 오염 감지 →
    모순 발견 시 전종목 강제 "판단보류" + 텔레그램 긴급 알림
      ↓
    네이버 수급 스크래핑 실패율 모니터링 (70% 이상 실패 시 텔레그램 경고)
      ↓
    포트폴리오 관점 진단 (PortfolioAnalyzer) — 테마/섹터 집중도, 당일 동조화율
      ↓
[3] 신호 점수 계산 (SignalScorer, 7차원)
    가격모멘텀 20% · 뉴스감성 15% · 거시정렬도 15% · 섹터강도 15% ·
    거래량신호 10% · 기술적신호 15% · 애널리스트신호 10%
      ↓
[4] 투자 판단 보조 등급 산정 (RatingAnalyzer)
    추천(75-100) | 안전(55-74) | 보통(35-54) | 주의(15-34) | 위험(0-14) | 판단보류
    (판단보류: 데이터 품질 문제로 시장 판단 자체를 보류한 상태 — 위험과 무관)
      ↓
[4-1] 등급 이력 저장 & 전일 대비 변화 감지, N일 전 등급 적중률 자기검증 (HistoryTracker)
      ↓
[5] Claude AI 리포트 생성 (ReportBuilder)
    글로벌 시장 개요 → 거시 신호 분석 → 섹터/테마 흐름 → 밸류체인 영향도
    → 관심종목 등급(수급·공시·지지저항·손익비·사용자 관전 포인트 포함)
    → 등급 적중률 자기검증 → 포트폴리오 관점 → 모니터링 포인트 → 투자 유의사항
      ↓
[6] 리포트 저장 (data/reports/YYYYMMDD_morning.md)
      ↓
[7] 이메일 발송 (선택, Gmail SMTP) — 주목 종목(추천/위험/판단보류 또는
    당일 등급변화·±5% 이상 급등락)의 일봉/주봉 캔들차트 인라인 첨부
```

---

## 📊 대시보드 탭 구성

| 탭 | 내용 |
|----|------|
| 📈 등급 현황 | 종목별 등급 카드, 점수 막대차트, 등급 분포 도넛 |
| 🌍 거시 지표 | S&P500/KOSPI/VIX/환율/금리/원자재/시장심리 |
| 📄 리포트 보기 | 방금 생성된 리포트 + 저장된 파일 목록 |
| 🔍 종목 상세 | 레이더 차트, 신호 컴포넌트, 지지/저항·손익비, 수급 동향, 긍정/부정/확인 요인 |
| 📊 등급 이력 | 등급 변화 요약, 종목별 점수 추이 차트, 데이터 품질 신뢰도 추이 |
| ⚙️ 설정 | 관심종목, 가중치, 등급 임계값, 테마 목록 확인 |
| 📋 종목 관리 | 관심종목·관심테마 추가/수정/삭제 (watchlist.json·themes.json 편집) |

---

## 🌐 관심종목 목록 (18개)

종목 구성은 `config/watchlist.json`에서 직접 수정하거나 대시보드의 **📋 종목 관리** 탭에서
편집할 수 있습니다. 종목별 `memo` 필드에 남긴 관전 포인트는 리포트 생성 시 자동 반영됩니다.

| 종목명 | 티커 | 국가 | 주요 테마 |
|--------|------|------|-----------|
| 삼성전자 | 005930 | 한국 | AI, 반도체, HBM |
| SK하이닉스 | 000660 | 한국 | AI, 반도체, HBM |
| KODEX 200 | 069500 | 한국 | 국내주식 ETF |
| LS ELECTRIC | 010120 | 한국 | 전력 인프라 |
| 한국전력 | 015760 | 한국 | 전력 인프라, 배당 |
| LG전자 | 066570 | 한국 | 가전, 전기차부품 |
| 오이솔루션 | 138080 | 한국(KOSDAQ) | 광통신 |
| NVIDIA | NVDA | 미국 | AI, 반도체, HBM |
| Invesco QQQ | QQQ | 미국 | 나스닥 ETF |
| Vanguard S&P500 ETF | VOO | 미국 | S&P500 ETF |
| Defiance Quantum ETF | QTUM | 미국 | 퀀텀컴퓨팅 ETF |
| Vistra Energy | VST | 미국 | 전력, 원자력 |
| Schwab US Dividend ETF | SCHD | 미국 | 배당 ETF |
| SanDisk | SNDK | 미국 | 낸드플래시, 스토리지 |
| Coherent Corp | COHR | 미국 | 광통신 |
| Ciena | CIEN | 미국 | 광통신, 네트워킹 |
| SpaceX | SPCX | 미국 | 우주항공, 위성인터넷 |
| TSMC | TSM | 대만 | 파운드리 |

---

## ⚠️ 투자 유의사항

> **본 시스템의 모든 등급은 "투자 판단 보조 등급"입니다.**
>
> 투자 권유 또는 매수·매도 신호가 아닙니다.
> 실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다.

**절대 금지 표현** — 시스템 리포트에 다음 표현은 절대 사용되지 않습니다:
`무조건 매수`, `반드시 매도`, `확실한 수익`, `손실 없음`, `보장`, `지금 사야 한다`, `지금 팔아야 한다`

**등급 기준**

| 등급 | 점수 | 의미 |
|------|------|------|
| 🟢 추천 | 75~100 | 우선 검토할 만한 상태 |
| 🔵 안전 | 55~74 | 리스크 낮고 안정적 |
| 🟡 보통 | 35~54 | 긍정·부정 신호 혼재 |
| 🟠 주의 | 15~34 | 단기 리스크 있음 |
| 🔴 위험 | 0~14 | 중대 리스크 감지 |
| ⚫ 판단보류 | — | 지수·ETF·대형주 데이터 간 모순 감지 시 전종목 강제 적용. 종목이 위험하다는 뜻이 아니라 **데이터 신뢰도 문제로 판단 자체를 보류**한 상태 |

등급 적중률(N일 전 등급이 이후 가격 기준으로 방향성이 맞았는지)은 리포트와 대시보드에서
자체 검증 통계로 함께 제공됩니다 — 참고용이며 미래 수익을 보장하지 않습니다.

---

## ✅ 운영 체크리스트

처음 실제 운영을 시작할 때, 아래 순서로 확인하세요.

### 1단계 — Mock 모드에서 동작 확인 (`.env`: `USE_MOCK_DATA=true`)
- [ ] `python app/healthcheck.py` — 설정 오류 없는지 확인
- [ ] `python app/main.py --report morning` — 아침 리포트 생성 확인
- [ ] `python app/main.py --report evening` — 저녁 리포트 생성 확인
- [ ] `streamlit run app/dashboard.py` — 대시보드에서 등급 카드 확인
- [ ] `data/reports/` — 리포트 파일(.md, .json) 생성 여부 확인
- [ ] `data/logs/market_flow.log` — 로그 파일 생성 여부 확인

### 2단계 — 실데이터 모드 전환 (`.env`: `USE_MOCK_DATA=false`)
- [ ] `USE_MOCK_DATA=false` 로 변경 후 저장
- [ ] `python app/main.py --report morning` 재실행
- [ ] 리포트 맨 아래 `[데이터 상태]` 섹션 확인
  - 가격 데이터: 실제 N개 수집 여부
  - 거시 지표: yfinance 실제 데이터 기준 표시 여부
  - **신뢰도 점수 70점 이상** 확인
- [ ] 로그에서 `[COLLECTOR_ERROR]` 또는 `[VALIDATION_WARNING]` 없는지 확인
- [ ] 대시보드에서 "데이터 신뢰도" 사이드바 점수 확인

### 3단계 — 이메일 발송 테스트
- [ ] `.env` 에 `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` 입력
- [ ] `python app/main.py --report morning --send-email` 실행
- [ ] 수신 이메일 확인 (스팸 폴더도 확인)
- [ ] 실패 시 `data/logs/market_flow.log` 에서 `[EMAIL_SEND_ERROR]` 확인

### 4단계 — 텔레그램 알림 테스트 (선택)
- [ ] `@BotFather` → `/newbot` → 토큰 발급
- [ ] 봇에 메시지 1회 전송 후 `chat_id` 확인
- [ ] `.env` 에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 입력
- [ ] `python app/main.py --report morning` 실행 → 알림 수신 여부 확인
  (등급 변화 또는 data_quality 급락 시에만 발송됩니다)
- [ ] 미수신 시 로그에서 `[TELEGRAM_NOTIFY_ERROR]` 확인

### 5단계 — DART 공시 연동 테스트 (선택)
- [ ] https://opendart.fss.or.kr 에서 무료 API 키 발급
- [ ] `.env` 에 `DART_API_KEY` 입력
- [ ] `python app/main.py --report morning` 실행 → 콘솔에 `공시 데이터: 연동 (N건)` 확인
- [ ] 리포트 하단 `[데이터 상태]` 표에서 "공시 연동: ✅ 연동됨" 확인

### 6단계 — 자동 실행 등록 (택 1)

**옵션 A — GitHub Actions (권장, PC를 꺼둬도 실행됨)**
- [ ] 저장소를 GitHub에 push
- [ ] Settings → Secrets and variables → Actions 에 필요한 값 등록 (위 "GitHub Actions 자동 실행" 참고)
- [ ] Actions 탭 → Run workflow로 수동 테스트 후 정상 완료 확인

**옵션 B — 로컬 Windows 작업 스케줄러 (PC가 항상 켜져 있어야 함)**
- [ ] `자동실행 설정.bat` 더블클릭 (또는 아래 수동 등록)
  ```
  작업 스케줄러 → 기본 작업 만들기
  → 트리거: 매일 07:00  → 동작: "스케줄러 실행.bat" 경로 입력
  ```
- [ ] 또는 `스케줄러 실행.bat` 수동 실행 후 07:00 / 18:30 알림 대기
- [ ] PC 절전모드를 사용하는 경우 "절전 모드 해제 후 실행" 옵션 활성화

### 7단계 — 로그 확인 위치
```
data/
└── logs/
    └── market_flow.log    ← 전체 실행 로그 (UTF-8, 누적 저장)
```

**로그 카테고리 키워드** (오류 검색 시 사용):
| 키워드 | 의미 |
|--------|------|
| `[COLLECTOR_ERROR]` | 가격·뉴스·거시 데이터 수집 실패 |
| `[VALIDATION_WARNING]` | 데이터 품질 경고, 등급 캡 적용 |
| `[CRITICAL_DATA_ERROR]` | 지수·ETF·대형주 데이터 모순 감지, 전종목 판단보류 |
| `[SCRAPER_ERROR]` | 네이버 수급 스크래핑 실패율 70% 이상 (페이지 구조 변경 의심) |
| `[PORTFOLIO_ANALYSIS_ERROR]` | 포트폴리오 집중도 진단 실패 (보조 기능, 파이프라인은 계속 진행) |
| `[CLAUDE_API_ERROR]` | Claude API 호출 실패 |
| `[EMAIL_SEND_ERROR]` | 이메일 발송 실패 |
| `[TELEGRAM_NOTIFY_ERROR]` | 텔레그램 알림 실패 |
| `[REPORT_SAVE_ERROR]` | 리포트 파일 저장 실패 |

---

## 🛠️ 문제 해결

### API 키가 로드되지 않을 때
`.env` 파일이 프로젝트 루트(`C:\Users\user\market_flow\.env`)에 있는지 확인하세요.

### yfinance 데이터 수집 실패 시
자동으로 Mock 데이터로 폴백됩니다. 인터넷 연결 상태를 확인하세요.

### 이메일 발송 실패 시
1. Gmail 앱 비밀번호가 올바른지 확인 (16자리, 공백 포함)
2. Gmail 2단계 인증이 활성화되어 있는지 확인
3. `python app/healthcheck.py`로 SMTP 연결 테스트

### 한글/이모지 출력 오류 (Windows)
배치파일에 `chcp 65001`이 포함되어 있습니다. 터미널에서 직접 실행 시 동일한 인코딩 문제가 발생하면:
```bash
chcp 65001
python app/main.py --report morning
```
