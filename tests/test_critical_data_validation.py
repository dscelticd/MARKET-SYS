"""
KOSPI 지수 데이터 오류 방어 로직 테스트

배경: 외부 API(yfinance ^KS11)에서 잘못된 지수 데이터가 들어왔을 때
      (예: KOSPI -9.99%인데 삼성전자/SK하이닉스/KODEX200은 상승)
      시스템이 그대로 리포트를 생성하지 않도록 방어하는 검증 로직.
"""
from __future__ import annotations

from app.utils.data_validator import DataValidator
from app.engine.rating_analyzer import apply_grade_cap, GRADE_META
from app.collectors.news_collector import classify_news_item
from app.engine.signal_scorer import SignalScorer


def _price_data(kodex=0.0, samsung=0.0, skhynix=0.0) -> dict:
    return {
        "KR_069500": {"change_pct": kodex,   "_mock": False},
        "KR_005930": {"change_pct": samsung, "_mock": False},
        "KR_000660": {"change_pct": skhynix, "_mock": False},
    }


def _macro_data(kospi_price, kospi_chg) -> dict:
    return {
        "kr_market": {"KOSPI": {"value": kospi_price, "change_pct": kospi_chg}},
        "_mock": False,
    }


_STOCKS = [{"id": "KR_069500"}, {"id": "KR_005930"}, {"id": "KR_000660"}]


# ── 테스트 1: KOSPI 비정상 수치 ──────────────────────────────────────────────

def test_kospi_out_of_range_is_critical():
    validator = DataValidator()
    quality = validator.validate(
        _price_data(), {}, _macro_data(8204, -9.99), _STOCKS,
    )
    assert quality["overall"]["critical_data_error"] is True

    ratings = [{"grade": "추천", "stock_id": "KR_005930"}]
    capped = apply_grade_cap(
        ratings, quality["overall"]["confidence"],
        critical_data_error=quality["overall"]["critical_data_error"],
    )
    assert capped[0]["grade"] == "판단보류"


# ── 테스트 2: KOSPI 급락, KODEX200 상승 ──────────────────────────────────────

def test_kospi_kodex200_mismatch_is_critical():
    validator = DataValidator()
    quality = validator.validate(
        _price_data(kodex=4.01), {}, _macro_data(2650, -9.99), _STOCKS,
    )
    assert quality["overall"]["critical_data_error"] is True
    reasons = " / ".join(quality["overall"]["critical_error_reasons"])
    assert "KODEX200" in reasons


# ── 테스트 3: 대형주 급등, 지수 급락 ─────────────────────────────────────────

def test_largecap_surge_vs_kospi_crash_is_critical():
    validator = DataValidator()
    quality = validator.validate(
        _price_data(samsung=9.84, skhynix=2.58), {}, _macro_data(2650, -9.99), _STOCKS,
    )
    assert quality["overall"]["critical_data_error"] is True
    reasons = " / ".join(quality["overall"]["critical_error_reasons"])
    assert "삼성전자" in reasons


# ── 테스트 4: 레버리지 뉴스 오분류 방지 ──────────────────────────────────────

def test_leverage_news_not_classified_as_direct_negative():
    result = classify_news_item("SK하이닉스 레버리지 25% 폭락")
    assert result["category"] == "파생상품 이슈"
    assert result["impact_to_underlying"] == "낮음"
    assert result["exclude_from_direct_negative_news"] is True

    # signal_scorer가 실제로 부정 요인 집계에서 제외하는지까지 확인
    scorer = SignalScorer()
    news_item = {
        "headline": "SK하이닉스 레버리지 25% 폭락",
        "sentiment": -0.8,
        "relevance": 0.9,
        "_mock": False,
        **result,
    }
    stock = {"id": "KR_000660", "name": "SK하이닉스", "sector": "반도체",
              "themes": ["AI", "반도체"], "country": "KR"}
    price_data = {"change_pct": 0.5, "volume_ratio": 1.0}
    _, negatives, checks = scorer._extract_factors(stock, price_data, [news_item], {}, {})
    assert not any("부정 뉴스" in n for n in negatives)
    assert any("파생상품" in c for c in checks)


# ── 정상 케이스 — 거짓 양성(false positive) 방지 확인 ───────────────────────

def test_normal_market_day_is_not_critical():
    validator = DataValidator()
    quality = validator.validate(
        _price_data(kodex=0.5, samsung=0.3, skhynix=0.2), {}, _macro_data(2650, 0.4), _STOCKS,
    )
    assert quality["overall"]["critical_data_error"] is False


def test_critical_error_does_not_break_normal_grade_cap_path():
    """critical_data_error=False일 때 기존 30/50점 등급 캡 로직이 그대로 동작해야 함"""
    ratings = [{"grade": "추천", "stock_id": "A"}, {"grade": "위험", "stock_id": "B"}]
    capped = apply_grade_cap(ratings, 20.0, critical_data_error=False)
    assert capped[0]["grade"] == "주의"   # 추천 → 주의 (quality < 30)
    assert capped[1]["grade"] == "위험"   # 위험은 캡 대상 아님


# ── 회귀 테스트: mock 가격 데이터 신뢰도 상한(cap) 버그 ──────────────────────
# 과거 max()를 사용해 mock 비중이 높을수록 오히려 price_score가 올라가는 역전이
# 있었음 (전종목 mock이어도 confidence 80점 "높음"으로 잘못 산정됨).

def test_all_mock_price_data_does_not_report_high_confidence():
    validator = DataValidator()
    stock_ids = [f"S{i}" for i in range(10)]
    stocks = [{"id": sid} for sid in stock_ids]
    price_all_mock = {sid: {"_mock": True} for sid in stock_ids}
    news = {sid: [{"headline": "x", "_mock": False}] for sid in stock_ids}
    macro_real = {
        "_mock": False, "us_market": {}, "kr_market": {},
        "currencies": {}, "rates": {}, "sentiment": {},
    }
    quality = validator.validate(price_all_mock, news, macro_real, stocks)
    assert quality["overall"]["confidence"] < 65
    assert quality["overall"]["status"] != "높음"


def test_single_stray_mock_does_not_crash_confidence():
    """mock 1개 정도는 신뢰도를 과도하게 깎지 않아야 함 (60점 cap이 전체를 덮지 않음)"""
    validator = DataValidator()
    stock_ids = [f"S{i}" for i in range(10)]
    stocks = [{"id": sid} for sid in stock_ids]
    price_mostly_real = {sid: {"_mock": (sid == "S0")} for sid in stock_ids}
    news = {sid: [{"headline": "x", "_mock": False}] for sid in stock_ids}
    macro_real = {
        "_mock": False, "us_market": {}, "kr_market": {},
        "currencies": {}, "rates": {}, "sentiment": {},
    }
    quality = validator.validate(price_mostly_real, news, macro_real, stocks)
    assert quality["overall"]["confidence"] > 60


# ── 회귀 테스트: 판단보류 등급이 고정 등급 목록에서 누락되지 않는지 확인 ──────

def test_report_builder_system_prompt_documents_pending_grade():
    from app.reports.report_builder import SYSTEM_PROMPT
    assert "판단보류" in SYSTEM_PROMPT


def test_signal_scorer_handles_nan_price_data_without_crash():
    """_risk_score / _extract_factors가 NaN을 조용히 0으로 처리해야 함 (크래시 금지)"""
    from app.engine.signal_scorer import SignalScorer
    scorer = SignalScorer()
    stock = {"id": "TEST", "name": "테스트", "sector": "반도체", "themes": [], "country": "KR"}
    price_nan = {"change_pct": float("nan"), "volume_ratio": float("nan")}

    risk = scorer._risk_score(price_nan, [], {})
    assert risk == 30.0  # NaN -> _sf 기본값 0 -> 리스크 가산 없음

    positives, negatives, _ = scorer._extract_factors(stock, price_nan, [], {}, {})
    assert not positives and not negatives


def test_grade_meta_has_judgement_pending():
    assert "판단보류" in GRADE_META


# ── 기준일(data_date) 불일치 시 교차검증 억제 ────────────────────────────────
# 배경: yfinance는 심볼마다 데이터 반영 시점이 달라, 같은 실행에서도 KOSPI는
# 8/27 바를, KODEX200은 8/28 바를 주는 일이 실제로 발생한다(2026-08-31 실측).
# 서로 다른 날의 등락률을 비교하면 당연히 어긋나는데, 이를 "데이터 모순"으로
# 판정해 전종목을 강제 "판단보류"로 강등시키는 허위 경보가 있었다.

def _dated_price(kodex_chg, kodex_date, samsung_date, skhynix_date) -> dict:
    return {
        "KR_069500": {"change_pct": kodex_chg, "_mock": False, "data_date": kodex_date},
        "KR_005930": {"change_pct": 0.0, "_mock": False, "data_date": samsung_date},
        "KR_000660": {"change_pct": 0.0, "_mock": False, "data_date": skhynix_date},
    }


def _dated_macro(kospi_chg, kospi_date) -> dict:
    return {
        "kr_market": {"KOSPI": {"value": 6900.0, "change_pct": kospi_chg}},
        "data_dates": {"KOSPI": kospi_date},
        "_mock": False,
    }


def test_mismatched_data_dates_suppress_false_critical_error():
    """기준일이 다르면 KOSPI↔KODEX200 불일치를 치명적 오류로 올리지 않는다."""
    critical, reasons, warnings = DataValidator._validate_kospi_consistency(
        _dated_price(-1.79, "2026-08-28", "2026-08-28", "2026-08-28"),
        _dated_macro(1.53, "2026-08-27"),
    )
    assert critical is False
    assert not reasons
    assert any("기준일 불일치" in w for w in warnings)


def test_same_data_date_still_detects_genuine_mismatch():
    """기준일이 같으면 진짜 모순은 여전히 치명적 오류로 잡아야 한다 (방어력 유지)."""
    critical, reasons, _ = DataValidator._validate_kospi_consistency(
        _dated_price(-1.79, "2026-08-27", "2026-08-27", "2026-08-27"),
        _dated_macro(1.53, "2026-08-27"),
    )
    assert critical is True
    assert any("KODEX200" in r for r in reasons)


def test_missing_data_date_falls_back_to_previous_behavior():
    """기준일 정보가 없는 과거 데이터는 기존처럼 교차검증을 수행한다."""
    critical, reasons, _ = DataValidator._validate_kospi_consistency(
        {"KR_069500": {"change_pct": -1.79, "_mock": False}},
        {"kr_market": {"KOSPI": {"value": 6900.0, "change_pct": 1.53}}, "_mock": False},
    )
    assert critical is True
    assert any("KODEX200" in r for r in reasons)
