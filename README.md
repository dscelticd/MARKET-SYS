# 📊 Market Flow Intelligence System

AI·반도체·HBM·데이터센터·전력 인프라 중심의 **개인 맞춤형 시장 브리핑 시스템**

Claude AI가 글로벌 시장 → 국가 흐름 → 섹터/테마 → 밸류체인 → 관심종목 순서로 분석하여
매일 아침/저녁 리포트를 자동 생성합니다.

---

## ⚡ 빠른 시작

```bash
# 1. 의존 패키지 설치
pip install anthropic yfinance streamlit plotly pandas schedule python-dotenv

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
├── app/
│   ├── main.py               # CLI 진입점
│   ├── dashboard.py          # Streamlit 대시보드
│   ├── scheduler.py          # 자동 스케줄러 (매일 07:00 / 18:30)
│   ├── healthcheck.py        # 시스템 상태 점검
│   ├── collectors/
│   │   ├── price_collector.py    # 주가 수집 (yfinance / Mock)
│   │   ├── news_collector.py     # 뉴스 수집 (yfinance / Mock)
│   │   └── macro_collector.py    # 거시지표 수집 (yfinance / Mock)
│   ├── engine/
│   │   ├── signal_scorer.py      # 5차원 신호 점수 계산
│   │   ├── rating_analyzer.py    # 등급 산정 (추천/안전/보통/주의/위험)
│   │   └── history_tracker.py    # 등급 이력 저장 & 변화 추적
│   ├── reports/
│   │   └── report_builder.py     # Claude API 리포트 생성
│   ├── delivery/
│   │   └── email_sender.py       # 이메일 발송 (Gmail SMTP)
│   └── utils/
│       └── config_loader.py      # 설정 파일 로더
├── config/
│   ├── watchlist.json        # 관심종목 목록 (12개)
│   ├── themes.json           # 관심테마 정의
│   ├── user_profile.json     # 투자성향 & 가중치
│   └── report_config.json    # 리포트 설정
├── data/
│   ├── reports/              # 생성된 리포트 (.md, .json)
│   ├── history/              # 등급 이력 (ratings_history.json)
│   └── cache/                # 캐시 데이터
├── .env                      # 환경변수 (API 키 등)
├── 아침 리포트 실행.bat
├── 아침 리포트+이메일.bat
├── 저녁 리포트 실행.bat
├── 저녁 리포트+이메일.bat
├── 대시보드 실행.bat
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

# ── 저장 경로 ──────────────────────────────────────
REPORT_SAVE_DIR=data/reports
REPORT_LANGUAGE=ko
```

> **Gmail 앱 비밀번호 발급**: Gmail → 구글 계정 → 보안 → 2단계 인증 활성화 → 앱 비밀번호 생성

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

---

## 📈 분석 파이프라인

```
[1] 설정 로드 (watchlist, themes, user_profile)
      ↓
[2] 데이터 수집
    - PriceCollector  → yfinance 실시간 주가 / 거래량
    - NewsCollector   → yfinance 뉴스 감성 분석
    - MacroCollector  → S&P500 / KOSPI / VIX / 환율 / 금리 / 원자재
      ↓
[3] 신호 점수 계산 (SignalScorer)
    - 가격 모멘텀     25%
    - 뉴스 감성       20%
    - 거시 정렬도     20%
    - 섹터 강도       20%
    - 거래량 신호     15%
      ↓
[4] 투자 판단 보조 등급 산정 (RatingAnalyzer)
    추천(75-100) | 안전(55-74) | 보통(35-54) | 주의(15-34) | 위험(0-14)
      ↓
[4-1] 등급 이력 저장 & 전일 대비 변화 감지 (HistoryTracker)
      ↓
[5] Claude AI 리포트 생성 (ReportBuilder)
    글로벌 시장 개요 → 거시 신호 분석 → 섹터/테마 흐름 → 밸류체인 영향도
    → 관심종목 등급 → 모니터링 포인트 → 투자 유의사항
      ↓
[6] 리포트 저장 (data/reports/YYYYMMDD_morning.md)
      ↓
[7] 이메일 발송 (선택, Gmail SMTP)
```

---

## 📊 대시보드 탭 구성

| 탭 | 내용 |
|----|------|
| 📈 등급 현황 | 종목별 등급 카드, 점수 막대차트, 등급 분포 도넛 |
| 🌍 거시 지표 | S&P500/KOSPI/VIX/환율/금리/원자재/시장심리 |
| 📄 리포트 보기 | 방금 생성된 리포트 + 저장된 파일 목록 |
| 🔍 종목 상세 | 레이더 차트, 신호 컴포넌트, 긍정/부정/확인 요인 |
| 📊 등급 이력 | 등급 변화 요약, 종목별 점수 추이 차트, 이력 테이블 |
| ⚙️ 설정 확인 | 관심종목, 가중치, 등급 임계값, 테마 목록 |

---

## 🌐 관심종목 목록

| 종목명 | 티커 | 국가 | 주요 테마 |
|--------|------|------|-----------|
| 삼성전자 | 005930.KS | 한국 | HBM, 스마트폰 |
| SK하이닉스 | 000660.KS | 한국 | HBM, AI 메모리 |
| LS ELECTRIC | 010120.KS | 한국 | 전력 인프라 |
| HD현대일렉트릭 | 267260.KS | 한국 | 전력 인프라 |
| 한화에어로스페이스 | 012450.KS | 한국 | 방산 |
| NVIDIA | NVDA | 미국 | AI, GPU |
| AMD | AMD | 미국 | AI, CPU/GPU |
| TSMC | TSM | 대만 | 파운드리 |
| ASML | ASML | 네덜란드 | 반도체 장비 |
| Microsoft | MSFT | 미국 | AI, 클라우드 |
| Alphabet | GOOGL | 미국 | AI, 클라우드 |
| Tesla | TSLA | 미국 | 전기차, AI |

---

## ⚠️ 투자 유의사항

> **본 시스템의 모든 등급은 "투자 판단 보조 등급"입니다.**
>
> 투자 권유 또는 매수·매도 신호가 아닙니다.
> 실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다.

**절대 금지 표현** — 시스템 리포트에 다음 표현은 절대 사용되지 않습니다:
`무조건 매수`, `반드시 매도`, `확실한 수익`, `손실 없음`, `보장`, `지금 사야 한다`, `지금 팔아야 한다`

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

### 5단계 — 스케줄러 등록 (Windows 작업 스케줄러)
- [ ] `자동실행 설정.bat` 더블클릭 (또는 아래 수동 등록)
  ```
  작업 스케줄러 → 기본 작업 만들기
  → 트리거: 매일 07:00  → 동작: "스케줄러 실행.bat" 경로 입력
  ```
- [ ] 또는 `스케줄러 실행.bat` 수동 실행 후 07:00 / 18:30 알림 대기
- [ ] PC 절전모드를 사용하는 경우 "절전 모드 해제 후 실행" 옵션 활성화

### 6단계 — 로그 확인 위치
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
