"""
Market Flow Intelligence System — Streamlit 대시보드 v3
실행: python -m streamlit run app/dashboard.py  또는  대시보드 실행.bat
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from datetime import time as dtime
from pathlib import Path
import os

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.graph_objects as go

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from app.utils.config_loader import (
    get_config, save_report_times,
    save_watchlist, save_themes, save_display_order,
)

# ── 타임존 ────────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
EDT = timezone(timedelta(hours=-4))   # 미국 동부 EDT (서머타임 기간)

def _now_kst() -> datetime: return datetime.now(KST)
def _now_et()  -> datetime: return datetime.now(EDT)

def _hex_rgba(hex_color: str, alpha: float = 0.18) -> str:
    """#RRGGBB → rgba(r,g,b,alpha) — Plotly 6.x fillcolor 호환"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"

def _is_mock_mode() -> bool:
    return os.getenv("USE_MOCK_DATA", "true").lower() == "true"

def _market_status() -> dict:
    """미국·한국 시장 현재 개장 여부"""
    now_et  = _now_et()
    now_kst = _now_kst()
    us_open = now_et.weekday() < 5 and dtime(9, 30) <= now_et.time() <= dtime(16, 0)
    kr_open = now_kst.weekday() < 5 and dtime(9, 0)  <= now_kst.time() <= dtime(15, 30)
    return {
        "us": {"open": us_open, "label": "개장중" if us_open else "마감",
               "time": now_et.strftime("%H:%M ET")},
        "kr": {"open": kr_open, "label": "개장중" if kr_open else "마감",
               "time": now_kst.strftime("%H:%M KST")},
    }


# ── 페이지 설정 (반드시 첫 번째 st 호출) ────────────────────────────────────
st.set_page_config(
    page_title="Market Flow Intelligence",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── 디자인 시스템 CSS ────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Market Flow  —  Design System v3
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ── 기본 레이아웃 ── */
.block-container {
    padding-top: 0.7rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 1440px !important;
}

/* ── 메트릭 카드 (라이트 테마 기준) ── */
[data-testid="stMetric"] {
    background: #ffffff !important;
    padding: 14px 18px !important;
    border-radius: 10px !important;
    border: 1px solid #e5e7eb !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06) !important;
}
/* 메트릭 텍스트 색상 명시 (다크모드 충돌 방지) */
[data-testid="stMetricLabel"] > div,
[data-testid="stMetricLabel"] p {
    color: #6b7280 !important;
    font-size: 0.80em !important;
    font-weight: 600 !important;
}
[data-testid="stMetricValue"] > div {
    color: #111827 !important;
    font-size: 1.55em !important;
    font-weight: 700 !important;
}
[data-testid="stMetricDelta"] svg { display: inline !important; }
[data-testid="stMetricDeltaIcon-Up"]   { color: #15803d !important; }
[data-testid="stMetricDeltaIcon-Down"] { color: #b91c1c !important; }

/* ── 구분선 ── */
hr {
    margin: 1.1rem 0 !important;
    border: none !important;
    border-top: 1px solid #e5e7eb !important;
}

/* ━━━━ Hero 헤더 ━━━━ */
.mf-hero {
    background: linear-gradient(120deg, #0f172a 0%, #1e3a5f 55%, #0f172a 100%);
    border-radius: 14px;
    padding: 18px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 14px;
    margin-bottom: 14px;
    box-shadow: 0 4px 20px rgba(15,23,42,0.18);
}
.mf-title {
    font-size: 1.35em;
    font-weight: 800;
    color: #f1f5f9;
    letter-spacing: -0.01em;
    line-height: 1.25;
}
.mf-subtitle {
    font-size: 0.74em;
    color: #64748b;
    margin-top: 5px;
    font-weight: 400;
}
.mf-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 7px;
}
.mf-datetime {
    font-size: 0.77em;
    color: #475569;
    font-weight: 500;
}
.mf-badges {
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
    justify-content: flex-end;
}
.mf-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 12px;
    border-radius: 9999px;
    font-size: 0.75em;
    font-weight: 600;
    white-space: nowrap;
}
.badge-open   { background: rgba(34,197,94,0.18); color: #4ade80; border: 1px solid rgba(34,197,94,0.28); }
.badge-closed { background: rgba(148,163,184,0.12); color: #94a3b8; border: 1px solid rgba(148,163,184,0.22); }

/* ━━━━ 면책 배너 ━━━━ */
.disclaimer {
    background: #fffbeb !important;
    border: 1px solid #fcd34d !important;
    border-radius: 9px;
    padding: 10px 16px;
    font-size: 0.80em;
    color: #78350f !important;
    margin-bottom: 14px;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    line-height: 1.5;
}

/* ━━━━ 등급 요약 Chips ━━━━ */
.chip-wrap {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 18px;
}
.chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 6px 15px;
    border-radius: 9999px;
    font-size: 0.82em;
    font-weight: 700;
    white-space: nowrap;
    border: 1.5px solid;
}
.chip-total {
    font-size: 0.76em;
    color: #9ca3af !important;
    font-weight: 500;
    margin-left: 4px;
}

/* ━━━━ 등급 카드 ━━━━ */
.gc {
    background: #ffffff !important;
    border-left: 4px solid;
    border-radius: 0 12px 12px 0;
    padding: 14px 16px;
    margin-bottom: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.07), 0 1px 2px rgba(0,0,0,0.04);
    transition: box-shadow 0.15s ease, transform 0.1s ease;
}
.gc:hover {
    box-shadow: 0 4px 14px rgba(0,0,0,0.11);
    transform: translateY(-1px);
}
.gc-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 7px;
    gap: 8px;
}
.gc-name {
    font-weight: 700;
    font-size: 0.94em;
    color: #111827 !important;
    line-height: 1.3;
}
.gc-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 3px;
    flex-shrink: 0;
}
.gc-badge {
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 0.76em;
    font-weight: 700;
    white-space: nowrap;
}
.gc-ticker {
    font-size: 0.71em;
    color: #9ca3af !important;
}
.gc-nums {
    display: flex;
    gap: 12px;
    font-size: 0.79em;
    margin-bottom: 7px;
    flex-wrap: wrap;
    align-items: center;
}
.gc-score  { font-weight: 700; color: #111827 !important; }
.gc-sub    { color: #6b7280 !important; }
.gc-chg-up   { color: #15803d !important; font-weight: 700; font-size: 0.78em; }
.gc-chg-down { color: #b91c1c !important; font-weight: 700; font-size: 0.78em; }
.gc-pos {
    font-size: 0.78em;
    color: #15803d !important;
    margin-bottom: 3px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
}
.gc-neg {
    font-size: 0.78em;
    color: #b91c1c !important;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
}

/* ━━━━ 섹션 라벨 ━━━━ */
.sec-lbl {
    font-size: 0.70em;
    font-weight: 700;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    color: #6b7280 !important;
    margin: 0 0 9px;
    display: flex;
    align-items: center;
    gap: 6px;
}

/* ━━━━ 거시 섹션 헤더 ━━━━ */
.macro-sec {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 2px solid #e5e7eb;
    flex-wrap: wrap;
}
.macro-sec-title {
    font-size: 0.90em;
    font-weight: 700;
    color: #1e293b !important;
}
.macro-badge {
    padding: 3px 10px;
    border-radius: 9999px;
    font-size: 0.74em;
    font-weight: 600;
}
.macro-open   { background: #dcfce7 !important; color: #15803d !important; }
.macro-closed { background: #f3f4f6 !important; color: #6b7280 !important; }

/* ━━━━ 공포탐욕 카드 ━━━━ */
.fg-card {
    background: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 12px;
    padding: 16px 18px;
    text-align: center;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.fg-lbl { font-size: 0.74em; color: #6b7280 !important; font-weight: 600; margin-bottom: 6px; }
.fg-val { font-size: 2.2em; font-weight: 800; line-height: 1; }
.fg-txt { font-size: 0.80em; color: #6b7280 !important; margin-top: 5px; }

/* ━━━━ 등급 이력 변화 카드 ━━━━ */
.chg-card {
    background: #ffffff !important;
    border: 1.5px solid;
    padding: 12px 14px;
    border-radius: 10px;
    text-align: center;
    margin-bottom: 4px;
}
.chg-name  { font-weight: 700; font-size: 0.88em; color: #111827 !important; margin-bottom: 5px; }
.chg-grade { font-weight: 700; font-size: 0.85em; }
.chg-pts   { font-size: 0.78em; color: #6b7280 !important; margin-top: 3px; }

/* ━━━━ 사이드바 ━━━━ */
[data-testid="stSidebar"] .stButton > button {
    width: 100%;
    text-align: center;
    margin-bottom: 6px;
    border-radius: 9px;
    font-weight: 600;
}
/* 사이드바 텍스트 명시 */
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stCaption {
    color: #374151 !important;
}
/* 탭 텍스트 */
.stTabs [data-baseweb="tab"] { color: #374151 !important; font-weight: 500; }
.stTabs [aria-selected="true"] { color: #111827 !important; font-weight: 700; }
/* st.info / st.success / st.warning 텍스트 가독성 */
[data-testid="stAlert"] p { color: inherit !important; }

/* ━━━━ 데이터프레임 ━━━━ */
[data-testid="stDataFrame"] {
    border-radius: 10px !important;
    overflow: hidden;
}

/* ━━━━ 알림 박스 ━━━━ */
[data-testid="stAlert"] {
    border-radius: 9px !important;
}

/* ━━━━ 탭 ━━━━ */
.stTabs [data-baseweb="tab-list"] {
    gap: 2px;
}
.stTabs [data-baseweb="tab"] {
    font-size: 0.87em;
    font-weight: 500;
    padding: 9px 16px;
    border-radius: 8px 8px 0 0;
}

/* ━━━━ 모바일 대응 ━━━━ */
@media (max-width: 768px) {
    .mf-hero { flex-direction: column; align-items: flex-start; }
    .mf-right { align-items: flex-start; }
    .mf-badges { justify-content: flex-start; }
    .mf-title  { font-size: 1.15em; }
    .chip-wrap { gap: 6px; }
    .chip { padding: 5px 11px; font-size: 0.78em; }
    .gc { padding: 12px 13px; }
    .gc-name { font-size: 0.88em; }
    .block-container { padding-left: 0.6rem !important; padding-right: 0.6rem !important; }
}
</style>
""", unsafe_allow_html=True)


# ── 등급 색상 팔레트 ──────────────────────────────────────────────────────────
GRADE_COLORS = {
    "추천": "#00C853", "안전": "#2979FF",
    "보통": "#FF9100", "주의": "#FF6D00", "위험": "#D50000",
}
GRADE_EMOJI = {"추천": "🟢", "안전": "🔵", "보통": "🟡", "주의": "🟠", "위험": "🔴"}

_CHIP_CSS = {
    "추천": "background:#f0fdf4;color:#15803d;border-color:#86efac;",
    "안전": "background:#eff6ff;color:#1d4ed8;border-color:#93c5fd;",
    "보통": "background:#fff7ed;color:#c2410c;border-color:#fdba74;",
    "주의": "background:#fff1f0;color:#9a3412;border-color:#fca5a5;",
    "위험": "background:#fef2f2;color:#991b1b;border-color:#f87171;",
}
_BADGE_CSS = {
    "추천": "background:#f0fdf4;color:#15803d;",
    "안전": "background:#eff6ff;color:#1d4ed8;",
    "보통": "background:#fff7ed;color:#c2410c;",
    "주의": "background:#fff1f0;color:#9a3412;",
    "위험": "background:#fef2f2;color:#991b1b;",
}


# ── 설정·데이터 헬퍼 ─────────────────────────────────────────────────────────

@st.cache_resource
def _load_config():
    return get_config()

def _invalidate_config():
    _load_config.clear()

def _list_report_files() -> list[Path]:
    return sorted(_load_config().report_save_dir().glob("*.md"), reverse=True)

def _load_latest_ratings(report_type: str) -> dict | None:
    cfg   = _load_config()
    files = sorted(cfg.report_save_dir().glob(f"*_{report_type}_ratings.json"), reverse=True)
    if not files:
        return None
    with open(files[0], encoding="utf-8") as f:
        d = json.load(f)
    mtime = datetime.fromtimestamp(files[0].stat().st_mtime, KST)
    d.setdefault("collected_at", mtime.strftime("%Y-%m-%d %H:%M KST"))
    return d


# ── 파이프라인 ────────────────────────────────────────────────────────────────

def run_pipeline(report_type: str, send_email: bool = False, save: bool = True) -> dict:
    from app.collectors.price_collector  import PriceCollector
    from app.collectors.news_collector   import NewsCollector
    from app.collectors.macro_collector  import MacroCollector
    from app.engine.signal_scorer        import SignalScorer
    from app.engine.rating_analyzer      import RatingAnalyzer
    from app.engine.history_tracker      import HistoryTracker
    from app.reports.report_builder      import ReportBuilder
    from app.reports.report_builder      import save_report as _save_report
    from app.delivery.email_sender       import EmailSender

    cfg       = _load_config()
    stocks    = cfg.watchlist.stocks
    stock_ids = [s["id"] for s in stocks]
    theme_map = {t["id"]: t for t in cfg.themes.themes}

    collected_at = _now_kst().strftime("%Y-%m-%d %H:%M KST")

    price_data = PriceCollector().collect(stock_ids)
    news_data  = NewsCollector().collect(stock_ids)
    macro_data = MacroCollector().collect()

    scorer  = SignalScorer(weights=cfg.user.signal_weights)
    analyzer = RatingAnalyzer()
    scores  = [scorer.score(s, price_data.get(s["id"], {}), news_data.get(s["id"], []),
                            macro_data, theme_map) for s in stocks]
    ratings = analyzer.analyze_batch(scores, stocks)
    r_dicts = [r.to_dict() for r in ratings]

    order_map = {sid: i for i, sid in enumerate(cfg.user.display_order)}
    r_dicts.sort(key=lambda r: order_map.get(r["stock_id"], 999))

    tracker       = HistoryTracker()
    grade_changes = tracker.get_changes(r_dicts, report_type)

    content, saved_path_str, email_result = None, None, None
    date_str = _now_kst().strftime("%Y-%m-%d")

    if save:
        tracker.save_today(r_dicts, report_type)
        builder = ReportBuilder()
        content = (
            builder.build_morning_report(price_data, news_data, macro_data, r_dicts,
                                         grade_changes=grade_changes)
            if report_type == "morning" else
            builder.build_evening_report(price_data, news_data, macro_data, r_dicts,
                                         grade_changes=grade_changes)
        )
        saved_path     = _save_report(content, report_type, cfg.report_save_dir())
        saved_path_str = str(saved_path)

        dp = _now_kst().strftime("%Y%m%d")
        jp = cfg.report_save_dir() / f"{dp}_{report_type}_ratings.json"
        jp.write_text(
            json.dumps({"date": date_str, "type": report_type,
                        "ratings": r_dicts, "macro": macro_data,
                        "collected_at": collected_at},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if send_email:
            sender = EmailSender()
            email_result = (
                ("성공" if sender.send_report(report_type, content, date_str) else "실패")
                if sender.is_configured() else "설정 미완료"
            )

    return {
        "date": date_str,
        "collected_at": collected_at,
        "type": report_type,
        "ratings": r_dicts,
        "macro": macro_data,
        "price": price_data,
        "report_content": content,
        "saved_path": saved_path_str,
        "email_result": email_result,
        "grade_changes": grade_changes,
    }


# ── UI 컴포넌트 ───────────────────────────────────────────────────────────────

def _render_hero() -> None:
    """상단 히어로 헤더 — 타이틀 + 현재 시각 + 시장 상태"""
    ms      = _market_status()
    now_str = _now_kst().strftime("%Y.%m.%d %H:%M KST")
    us_cls  = "badge-open"   if ms["us"]["open"] else "badge-closed"
    kr_cls  = "badge-open"   if ms["kr"]["open"] else "badge-closed"
    us_dot  = "🟢" if ms["us"]["open"] else "⚫"
    kr_dot  = "🟢" if ms["kr"]["open"] else "⚫"
    st.markdown(f"""
<div class="mf-hero">
  <div>
    <div class="mf-title">📊 Market Flow Intelligence</div>
    <div class="mf-subtitle">AI · 반도체 · HBM · 데이터센터 · 전력 인프라 | 개인 맞춤형 시장 브리핑</div>
  </div>
  <div class="mf-right">
    <div class="mf-datetime">{now_str}</div>
    <div class="mf-badges">
      <span class="mf-badge {us_cls}">{us_dot} 🇺🇸 {ms["us"]["label"]} &nbsp;{ms["us"]["time"]}</span>
      <span class="mf-badge {kr_cls}">{kr_dot} 🇰🇷 {ms["kr"]["label"]} &nbsp;{ms["kr"]["time"]}</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)


def _render_disclaimer() -> None:
    st.markdown("""
<div class="disclaimer">
  ⚠️&nbsp;
  <span>모든 등급은 <b>투자 판단 보조 참고 자료</b>입니다.
  실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다.
  투자 원금 손실이 발생할 수 있습니다.</span>
</div>
""", unsafe_allow_html=True)


def _render_grade_chips(ratings: list[dict]) -> None:
    """등급별 종목 수를 Chip 형태로 표시"""
    dist  = Counter(r["grade"] for r in ratings)
    chips = []
    for grade in ["추천", "안전", "보통", "주의", "위험"]:
        cnt = dist.get(grade, 0)
        if cnt > 0:
            css   = _CHIP_CSS[grade]
            emoji = GRADE_EMOJI[grade]
            chips.append(
                f'<span class="chip" style="{css}">'
                f'{emoji} {grade}&nbsp;<b>{cnt}</b></span>'
            )
    if chips:
        total = len(ratings)
        chips.append(
            f'<span class="chip-total">총 {total}개 종목</span>'
        )
        st.markdown(
            f'<div class="chip-wrap">{"".join(chips)}</div>',
            unsafe_allow_html=True,
        )


def _render_grade_card(r: dict, change: dict | None = None) -> None:
    grade  = r["grade"]
    color  = GRADE_COLORS[grade]
    emoji  = GRADE_EMOJI[grade]
    badgst = _BADGE_CSS[grade]

    delta_html = ""
    if change and change["direction"] in ("상승", "하락"):
        cls   = "gc-chg-up" if change["direction"] == "상승" else "gc-chg-down"
        arrow = "↑" if change["direction"] == "상승" else "↓"
        delta_html = (
            f'&ensp;<span class="{cls}">'
            f'{arrow} {change["prev_grade"]}→{change["curr_grade"]}</span>'
        )

    pos_txt = r["positive_factors"][0] if r["positive_factors"] else "—"
    neg_txt = r["negative_factors"][0] if r["negative_factors"] else "—"
    if len(pos_txt) > 46: pos_txt = pos_txt[:46] + "…"
    if len(neg_txt) > 46: neg_txt = neg_txt[:46] + "…"

    st.markdown(f"""
<div class="gc" style="border-color:{color};">
  <div class="gc-top">
    <div>
      <div class="gc-name">{emoji} {r['name']}{delta_html}</div>
    </div>
    <div class="gc-right">
      <span class="gc-badge" style="{badgst}">{grade}</span>
      <span class="gc-ticker">{r['ticker']}</span>
    </div>
  </div>
  <div class="gc-nums">
    <span class="gc-score">점수 {r['total_score']:.0f}</span>
    <span class="gc-sub">리스크 {r['risk_score']:.0f}</span>
    <span class="gc-sub">신뢰도 {r['data_confidence']:.0f}</span>
  </div>
  <div class="gc-pos">✅ {pos_txt}</div>
  <div class="gc-neg">⚠️ {neg_txt}</div>
</div>""", unsafe_allow_html=True)


def _render_score_chart(ratings: list[dict]) -> None:
    names  = [r["name"] for r in ratings]
    scores = [r["total_score"] for r in ratings]
    colors = [GRADE_COLORS[r["grade"]] for r in ratings]
    fig = go.Figure(go.Bar(
        x=scores, y=names, orientation="h",
        marker_color=colors,
        marker_line_width=0,
        text=[f"<b>{s:.0f}</b>" for s in scores],
        textposition="outside",
    ))
    fig.update_layout(
        title=dict(text="투자 판단 보조 점수 (0~100)", font_size=13, font_color="#374151"),
        xaxis=dict(range=[0, 120], showgrid=True, gridcolor="#f0f0f0", zeroline=False),
        yaxis=dict(autorange="reversed", tickfont=dict(size=12)),
        height=max(320, len(ratings) * 30 + 80),
        margin=dict(l=10, r=75, t=44, b=20),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    for v, lbl, c in [(75, "추천", "#00C853"), (55, "안전", "#2979FF"),
                      (35, "보통", "#FF9100"), (15, "주의", "#FF6D00")]:
        fig.add_vline(
            x=v, line_dash="dot", line_color=c, line_width=1.5,
            annotation_text=lbl, annotation_font_color=c,
            annotation_font_size=10, annotation_yshift=6,
        )
    st.plotly_chart(fig, width="stretch")


def _render_grade_donut(ratings: list[dict]) -> None:
    dist = Counter(r["grade"] for r in ratings)
    keys = [g for g in ["추천", "안전", "보통", "주의", "위험"] if g in dist]
    fig  = go.Figure(go.Pie(
        labels=keys,
        values=[dist[g] for g in keys],
        hole=0.58,
        marker_colors=[GRADE_COLORS[g] for g in keys],
        textinfo="label+value",
        textfont=dict(size=12),
        hovertemplate="%{label}: %{value}개<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="등급 분포", font_size=13, font_color="#374151"),
        height=270,
        margin=dict(l=10, r=10, t=44, b=10),
        showlegend=False,
        paper_bgcolor="white",
    )
    st.plotly_chart(fig, width="stretch")


def _render_radar_chart(r: dict) -> None:
    cats  = list(r["components"].keys())
    vals  = [r["components"][c] for c in cats] + [r["components"][cats[0]]]
    color = GRADE_COLORS[r["grade"]]
    fig = go.Figure(go.Scatterpolar(
        r=vals, theta=cats + [cats[0]], fill="toself",
        line_color=color,
        line_width=2,
        fillcolor=_hex_rgba(color, 0.18),
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100], tickfont=dict(size=9)),
            angularaxis=dict(tickfont=dict(size=11)),
        ),
        showlegend=False,
        height=310,
        margin=dict(l=24, r=24, t=40, b=24),
        title=dict(text=f"{r['name']} 신호 분석", font_size=13, font_color="#374151"),
        paper_bgcolor="white",
    )
    st.plotly_chart(fig, width="stretch")


def _fmt_num(v, fmt=".1f") -> str:
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return str(v)


def _render_macro_panel(macro: dict, collected_at: str = "") -> None:
    us    = macro.get("us_market", {})
    kr    = macro.get("kr_market", {})
    cur   = macro.get("currencies", {})
    rates = macro.get("rates", {})
    sent  = macro.get("sentiment", {})
    comm  = macro.get("commodities", {})
    ms    = _market_status()

    def _sec(flag: str, title: str, status: dict) -> None:
        cls = "macro-open" if status["open"] else "macro-closed"
        lbl = status["label"]
        t   = status["time"]
        st.markdown(
            f'<div class="macro-sec">'
            f'<span style="font-size:1.25em;">{flag}</span>'
            f'<span class="macro-sec-title">{title}</span>'
            f'<span class="macro-badge {cls}">{lbl} · {t}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 미국 시장 ──────────────────────────────────────────────────────────────
    _sec("🇺🇸", "미국 시장", ms["us"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("S&P 500",   _fmt_num(us.get("SP500",{}).get("value","N/A"),  ",.1f"),
              f'{us.get("SP500",{}).get("change_pct",0):+.2f}%')
    c2.metric("NASDAQ",    _fmt_num(us.get("NASDAQ",{}).get("value","N/A"), ",.1f"),
              f'{us.get("NASDAQ",{}).get("change_pct",0):+.2f}%')
    c3.metric("SOX",       _fmt_num(us.get("SOX",{}).get("value","N/A"),    ",.1f"),
              f'{us.get("SOX",{}).get("change_pct",0):+.2f}%')
    c4.metric("VIX",       us.get("VIX",{}).get("value","N/A"),
                           us.get("VIX",{}).get("signal",""))
    st.divider()

    # ── 한국 시장 ──────────────────────────────────────────────────────────────
    _sec("🇰🇷", "한국 시장", ms["kr"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("KOSPI",    _fmt_num(kr.get("KOSPI",{}).get("value","N/A"),  ",.2f"),
              f'{kr.get("KOSPI",{}).get("change_pct",0):+.2f}%')
    c2.metric("KOSDAQ",   _fmt_num(kr.get("KOSDAQ",{}).get("value","N/A"), ",.2f"),
              f'{kr.get("KOSDAQ",{}).get("change_pct",0):+.2f}%')
    c3.metric("USD/KRW",  _fmt_num(cur.get("USD_KRW",{}).get("value","N/A"), ",.1f"))
    fnet = kr.get("foreign_net_buy_bn", "N/A")
    c4.metric("외국인 순매수",
              f"{fnet:+,.0f}억" if isinstance(fnet, (int, float)) else "N/A")
    st.divider()

    # ── 환율·금리·원자재 ────────────────────────────────────────────────────────
    st.markdown('<p class="sec-lbl">💱 &nbsp;환율 · 금리 · 원자재</p>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("DXY (달러지수)",   _fmt_num(cur.get("DXY",{}).get("value","N/A"), ".2f"),
              cur.get("DXY",{}).get("signal",""))
    c2.metric("미 10년물 금리",   f'{rates.get("us_10y_yield",{}).get("value","N/A")}%')
    c3.metric("구리",             f'${comm.get("copper",{}).get("value","N/A")}')
    c4.metric("WTI 원유",         f'${comm.get("WTI_oil",{}).get("value","N/A")}')
    st.divider()

    # ── 시장 심리 ───────────────────────────────────────────────────────────────
    st.markdown('<p class="sec-lbl">🧭 &nbsp;시장 심리 지표</p>', unsafe_allow_html=True)
    fg     = sent.get("fear_greed_index", {})
    fg_val = fg.get("value", 50)
    fc     = "#15803d" if fg_val >= 60 else ("#b91c1c" if fg_val <= 30 else "#c2410c")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f'<div class="fg-card">'
            f'<div class="fg-lbl">공포·탐욕 지수</div>'
            f'<div class="fg-val" style="color:{fc};">{fg_val}</div>'
            f'<div class="fg-txt">{fg.get("label","")}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    c2.metric("글로벌 리스크 성향",  sent.get("global_risk_appetite", "N/A"))
    c3.metric("AI CapEx 사이클",     sent.get("ai_capex_cycle",       "N/A"))
    c4.metric("반도체 사이클",       sent.get("semiconductor_cycle",  "N/A"))

    if collected_at:
        st.caption(f"📅 데이터 수집 기준: **{collected_at}**")


# ── 종목 관리 탭 ─────────────────────────────────────────────────────────────

# 국가·시장·통화 옵션
_COUNTRIES = ["KR", "US", "TW", "JP", "NL", "DE", "FR", "CN", "HK", "SG"]
_MARKETS   = ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE", "TSE", "TWSE", "AEX", "기타"]
_CURRENCIES = ["KRW", "USD", "TWD", "JPY", "EUR", "HKD", "CNY"]
_STATUSES  = ["보유", "관찰", "예정", "매도완료"]
_MACRO_SENS = ["high", "medium", "low"]


def _wl_to_df(stocks: list[dict]) -> pd.DataFrame:
    """watchlist 리스트 → 편집용 DataFrame"""
    return pd.DataFrame([{
        "ID":    s["id"],
        "종목명": s["name"],
        "티커":  s["ticker"],
        "국가":  s["country"],
        "시장":  s.get("market", ""),
        "통화":  s.get("currency", "KRW"),
        "섹터":  s.get("sector", ""),
        "테마":  ", ".join(s.get("themes", [])),
        "관심도": int(s.get("interest_level", 3)),
        "상태":  s.get("status", "관찰"),
        "메모":  s.get("memo", ""),
    } for s in stocks])


def _df_to_wl(edited: pd.DataFrame, original: list[dict]) -> list[dict]:
    """편집된 DataFrame → watchlist 리스트 (기존 상세 필드 보존)"""
    orig_map = {s["id"]: s for s in original}
    result   = []
    for _, row in edited.iterrows():
        ticker  = str(row.get("티커", "")).strip()
        name    = str(row.get("종목명", "")).strip()
        if not ticker or not name:
            continue                          # 빈 행 스킵
        country = str(row.get("국가", "KR")).strip()
        row_id  = str(row.get("ID", "")).strip()
        if not row_id:                        # 신규 종목: ID 자동 생성
            row_id = f"{country}_{ticker.upper()}"
        base = orig_map.get(row_id, {})       # 기존 상세 필드 가져오기
        result.append({
            "id":      row_id,
            "name":    name,
            "ticker":  ticker,
            "country": country,
            "market":  str(row.get("시장", base.get("market", ""))).strip(),
            "currency": str(row.get("통화", base.get("currency", "KRW"))).strip(),
            "sector":  str(row.get("섹터", "")).strip(),
            "industry": base.get("industry", ""),
            "themes":  [t.strip() for t in str(row.get("테마", "")).split(",") if t.strip()],
            "related_markets":   base.get("related_markets",   []),
            "related_companies": base.get("related_companies", []),
            "macro_variables":   base.get("macro_variables",   []),
            "interest_level": max(1, min(5, int(row.get("관심도", 3)))),
            "status": str(row.get("상태", "관찰")).strip(),
            "memo":   str(row.get("메모", "")).strip(),
        })
    return result


def _render_watchlist_manager() -> None:
    """📋 종목 관리 탭 전체 렌더링"""
    cfg = _load_config()

    st.subheader("📋 관심종목 관리")
    st.markdown(
        '<div style="background:#eff6ff;border:1px solid #93c5fd;border-radius:8px;'
        'padding:10px 16px;font-size:0.81em;color:#1e40af;margin-bottom:16px;">'
        '💡 <b>사용 방법</b> — 셀을 클릭해 직접 편집하거나, '
        '맨 아래 <b>빈 행</b>에 새 종목을 입력하세요. '
        '행 왼쪽 체크박스 선택 후 <kbd>Delete</kbd> 키로 삭제할 수 있습니다. '
        '편집 후 반드시 <b>💾 저장</b>을 눌러야 반영됩니다.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── 섹션 1: 관심종목 편집 ─────────────────────────────────────────────────
    stocks = cfg.watchlist.stocks

    edited_wdf = st.data_editor(
        _wl_to_df(stocks),
        column_config={
            "ID": st.column_config.TextColumn(
                label="ID",
                disabled=True,
                width="small",
                help="자동 생성 (변경 불가) — 신규 종목은 저장 시 자동 부여됩니다",
            ),
            "종목명": st.column_config.TextColumn(
                label="종목명",
                required=True,
                max_chars=40,
                width="medium",
            ),
            "티커": st.column_config.TextColumn(
                label="티커",
                required=True,
                max_chars=20,
                width="small",
                help="예) 005930 / NVDA / TSM",
            ),
            "국가": st.column_config.SelectboxColumn(
                label="국가",
                options=_COUNTRIES,
                required=True,
                width="small",
            ),
            "시장": st.column_config.SelectboxColumn(
                label="시장",
                options=_MARKETS,
                width="small",
            ),
            "통화": st.column_config.SelectboxColumn(
                label="통화",
                options=_CURRENCIES,
                width="small",
            ),
            "섹터": st.column_config.TextColumn(
                label="섹터",
                max_chars=30,
                width="medium",
                help="예) 반도체 / 전력 인프라 / 빅테크",
            ),
            "테마": st.column_config.TextColumn(
                label="테마 (쉼표 구분)",
                width="large",
                help="예) AI, 반도체, HBM",
            ),
            "관심도": st.column_config.NumberColumn(
                label="관심도",
                min_value=1,
                max_value=5,
                step=1,
                format="%d ⭐",
                width="small",
            ),
            "상태": st.column_config.SelectboxColumn(
                label="상태",
                options=_STATUSES,
                width="small",
            ),
            "메모": st.column_config.TextColumn(
                label="메모",
                width="large",
                max_chars=200,
            ),
        },
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        key="wl_editor",
    )

    # 저장 버튼
    sa, sb = st.columns([1, 4])
    with sa:
        save_wl = st.button("💾 종목 목록 저장", type="primary", width="stretch")
    with sb:
        st.caption(
            f"현재 {len(stocks)}개 종목 등록 | "
            "추가 후 저장 → 다음 '🔄 등급 즉시 새로고침' 실행 시 새 종목이 분석에 반영됩니다."
        )

    if save_wl:
        new_stocks = _df_to_wl(edited_wdf, stocks)
        if not new_stocks:
            st.error("❌ 저장할 종목이 없습니다. 종목명과 티커를 입력해 주세요.")
        else:
            save_watchlist(new_stocks)
            save_display_order([s["id"] for s in new_stocks])
            # 대시보드 데이터 초기화 → 다음 실행 시 새 종목 반영
            st.session_state.pop("dashboard_data", None)
            _invalidate_config()
            st.success(f"✅ {len(new_stocks)}개 종목 저장 완료! 좌측 '🔄 등급 즉시 새로고침'을 실행하세요.")
            st.rerun()

    st.divider()

    # ── 섹션 2: 관심 테마 편집 ───────────────────────────────────────────────
    st.markdown("#### 🎨 관심 테마 관리")
    st.caption("테마 기본 정보를 편집합니다. 관련 종목·주요 드라이버 등 상세 항목은 config/themes.json에서 직접 수정하세요.")

    themes      = cfg.themes.themes
    theme_ids   = {t["id"]: t for t in themes}

    tdf = pd.DataFrame([{
        "ID":       t["id"],
        "테마명":   t["name"],
        "설명":     t.get("description", ""),
        "관심도":   int(t.get("interest_level", 3)),
        "거시 민감도": t.get("macro_sensitivity", "medium"),
        "관련 종목 수": len(t.get("related_stocks", [])),
    } for t in themes])

    edited_tdf = st.data_editor(
        tdf,
        column_config={
            "ID": st.column_config.TextColumn(
                label="ID", disabled=True, width="small",
                help="테마 ID — 변경 불가",
            ),
            "테마명": st.column_config.TextColumn(
                label="테마명", required=True, max_chars=30, width="medium",
            ),
            "설명": st.column_config.TextColumn(
                label="설명", max_chars=100, width="large",
            ),
            "관심도": st.column_config.NumberColumn(
                label="관심도", min_value=1, max_value=5, step=1,
                format="%d ⭐", width="small",
            ),
            "거시 민감도": st.column_config.SelectboxColumn(
                label="거시 민감도",
                options=_MACRO_SENS,
                width="small",
            ),
            "관련 종목 수": st.column_config.NumberColumn(
                label="관련 종목 수", disabled=True, width="small",
            ),
        },
        num_rows="fixed",
        width="stretch",
        hide_index=True,
        key="theme_editor",
    )

    tc1, tc2 = st.columns([1, 4])
    with tc1:
        save_th = st.button("💾 테마 저장", type="secondary", width="stretch")
    with tc2:
        st.caption("테마 추가·삭제는 config/themes.json을 직접 편집하세요.")

    if save_th:
        new_themes = []
        for _, row in edited_tdf.iterrows():
            tid   = str(row.get("ID", "")).strip()
            tname = str(row.get("테마명", "")).strip()
            if not tid or not tname:
                continue
            base = theme_ids.get(tid, {})
            new_themes.append({
                "id":          tid,
                "name":        tname,
                "description": str(row.get("설명", base.get("description", ""))).strip(),
                "sub_themes":  base.get("sub_themes",     []),
                "key_drivers": base.get("key_drivers",    []),
                "key_risks":   base.get("key_risks",      []),
                "related_sectors": base.get("related_sectors", []),
                "related_stocks":  base.get("related_stocks",  []),
                "macro_sensitivity": str(row.get("거시 민감도", "medium")).strip(),
                "interest_level": max(1, min(5, int(row.get("관심도", 3)))),
            })
        if new_themes:
            save_themes(new_themes)
            _invalidate_config()
            st.success(f"✅ {len(new_themes)}개 테마 저장 완료!")
            st.rerun()
        else:
            st.error("❌ 저장할 테마가 없습니다.")

    st.divider()

    # ── 섹션 3: 표시 순서 조정 ────────────────────────────────────────────────
    st.markdown("#### 🔢 등급 현황 표시 순서")
    st.caption(
        "종목이 등급 현황 탭에 표시되는 순서를 조정합니다. "
        "위로 이동할 종목을 선택하고 ↑ 버튼을 누르세요."
    )

    current_stocks  = _load_config().watchlist.stocks
    current_ids     = [s["id"] for s in current_stocks]
    name_map        = {s["id"]: s["name"] for s in current_stocks}
    display_order   = _load_config().user.display_order or current_ids

    # display_order에 없는 ID는 뒤에 추가
    ordered = [sid for sid in display_order if sid in set(current_ids)]
    ordered += [sid for sid in current_ids if sid not in set(ordered)]

    sel_move = st.selectbox(
        "순서를 변경할 종목",
        options=ordered,
        format_func=lambda x: f"{name_map.get(x, x)}  ({x})",
        label_visibility="collapsed",
    )

    mc1, mc2, mc3 = st.columns([1, 1, 6])
    with mc1:
        up_btn = st.button("⬆️ 위로", width="stretch")
    with mc2:
        dn_btn = st.button("⬇️ 아래로", width="stretch")
    with mc3:
        # 현재 순서 미리보기
        preview = "  →  ".join(
            [f"**{name_map.get(s, s)}**" if s == sel_move else name_map.get(s, s)
             for s in ordered]
        )
        st.caption(f"현재 순서: {preview}")

    if up_btn or dn_btn:
        idx = ordered.index(sel_move)
        if up_btn and idx > 0:
            ordered[idx], ordered[idx - 1] = ordered[idx - 1], ordered[idx]
        elif dn_btn and idx < len(ordered) - 1:
            ordered[idx], ordered[idx + 1] = ordered[idx + 1], ordered[idx]
        save_display_order(ordered)
        _invalidate_config()
        st.rerun()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    cfg = _load_config()

    # ── 히어로 헤더 + 면책 배너 ──
    _render_hero()
    _render_disclaimer()

    # ── 사이드바 ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🎛️ 컨트롤 패널")

        report_type = st.radio(
            "리포트 유형",
            ["morning", "evening"],
            format_func=lambda x: "🌅 아침 브리핑" if x == "morning" else "🌙 저녁 결산",
            horizontal=True,
        )
        st.markdown("---")

        send_email = st.toggle("📧 이메일 발송", value=False)
        run_btn = st.button(
            f"{'🌅' if report_type == 'morning' else '🌙'} 리포트 생성 + 저장",
            type="primary",
            width="stretch",
            help="Claude AI 분석 리포트 생성 + 파일 저장 (약 30~60초)",
        )
        refresh_btn = st.button(
            "🔄 등급 즉시 새로고침",
            width="stretch",
            help="리포트 저장 없이 등급·거시지표 빠르게 업데이트 (약 10~15초)",
        )
        st.markdown("---")

        # ── 자동 새로고침 ──
        auto_refresh = st.toggle("⏱️ 자동 새로고침", value=False)
        if auto_refresh:
            refresh_min = st.select_slider(
                "새로고침 간격",
                options=[5, 10, 15, 30, 60],
                value=15,
                format_func=lambda x: f"{x}분",
            )
            components.html(
                f"<script>setTimeout(()=>window.parent.location.reload(),"
                f"{refresh_min * 60_000});</script>",
                height=0,
            )
            st.caption(f"⏳ {refresh_min}분 후 자동 갱신")
        st.markdown("---")

        # ── 실행 결과 상태 ──
        if "last_run" in st.session_state:
            st.success(f"✅ {st.session_state['last_run']}")
        if "last_email" in st.session_state:
            r  = st.session_state["last_email"]
            fn = st.success if r == "성공" else (st.warning if r == "설정 미완료" else st.error)
            fn(f"📧 이메일: {r}")
        st.markdown("---")

        # ── 데이터 모드 ──
        if _is_mock_mode():
            st.warning("🔧 **Mock 모드**\n\n`.env` → `USE_MOCK_DATA=false`")
        else:
            st.success("📡 **실시간 데이터** — yfinance")

        st.caption(
            f"⏰ 자동 발송\n"
            f"🌅 {cfg.user.morning_time} / 🌙 {cfg.user.evening_time}\n\n"
            f"*(⚙️ 설정 탭에서 변경)*"
        )

    # ── 버튼 처리 ─────────────────────────────────────────────────────────────
    if run_btn:
        label = "아침 브리핑" if report_type == "morning" else "저녁 결산"
        with st.spinner(f"⏳ {label} 생성 중… Claude AI 분석 중 (약 30~60초)"):
            data = run_pipeline(report_type, send_email, save=True)
        st.session_state["dashboard_data"] = data
        st.session_state["last_run"]        = f"{data['collected_at']} ({label})"
        if send_email:
            st.session_state["last_email"] = data.get("email_result", "—")
        st.success(f"✅ 생성 완료 → {data['saved_path']}")
        st.rerun()

    if refresh_btn:
        with st.spinner("🔄 실시간 데이터 수집 및 등급 재계산 중 (약 10~15초)…"):
            data = run_pipeline(report_type, send_email=False, save=False)
        st.session_state["dashboard_data"] = data
        st.session_state["last_run"]        = f"{data['collected_at']} (새로고침)"
        st.rerun()

    # ── 데이터 로드 ───────────────────────────────────────────────────────────
    data = st.session_state.get("dashboard_data")
    if not data:
        saved = _load_latest_ratings(report_type)
        if saved:
            data = saved
            data.setdefault("price", {})
            st.session_state["dashboard_data"] = data

    if not data:
        st.info("👈 왼쪽 사이드바 버튼으로 시작하세요.")
        ca, cb = st.columns(2)
        with ca:
            st.markdown("""
**🔄 등급 즉시 새로고침** *(약 10~15초)*
- 실시간 주가·거시지표 수집
- 종목 등급 재계산
- 리포트 파일 저장 없음
""")
        with cb:
            st.markdown(f"""
**{'🌅' if report_type == 'morning' else '🌙'} 리포트 생성 + 저장** *(약 30~60초)*
- 등급 계산 + Claude AI 심층 분석
- 마크다운 파일 저장
- 이메일 발송 옵션
""")
        return

    ratings       = data.get("ratings", [])
    macro         = data.get("macro", {})
    collected_at  = data.get("collected_at", "—")
    grade_changes = data.get("grade_changes", [])
    changes_map   = {c["stock_id"]: c for c in grade_changes} if grade_changes else {}

    # ── 탭 ───────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📈 등급 현황", "🌍 거시 지표", "📄 리포트 보기",
        "🔍 종목 상세", "📊 등급 이력", "⚙️ 설정", "📋 종목 관리",
    ])

    # ── Tab 1: 등급 현황 ──────────────────────────────────────────────────────
    with tab1:
        # 헤더 행
        hc1, hc2 = st.columns([2, 1])
        with hc1:
            st.subheader("투자 판단 보조 등급")
        with hc2:
            st.markdown(
                f'<div style="text-align:right;padding-top:0.55rem;">'
                f'<span style="font-size:0.79em;color:#6b7280;">기준: <b>{collected_at}</b></span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # 등급 요약 Chips
        _render_grade_chips(ratings)

        # 차트 행 (점수 바차트 + 도넛)
        cc1, cc2 = st.columns([3, 1])
        with cc1:
            _render_score_chart(ratings)
        with cc2:
            _render_grade_donut(ratings)
            # 등급 변화 요약
            changed = [c for c in grade_changes if c["direction"] in ("상승", "하락")]
            if changed:
                st.markdown("**📌 등급 변화**")
                for c in changed:
                    clr  = "#15803d" if c["direction"] == "상승" else "#b91c1c"
                    icon = "📈" if c["direction"] == "상승" else "📉"
                    st.markdown(
                        f'<div style="font-size:0.82em;color:{clr};margin-bottom:3px;">'
                        f'{icon} <b>{c["name"]}</b>: {c["prev_grade"]} → {c["curr_grade"]}'
                        f' <span style="color:#9ca3af;">({c["score_delta"]:+.0f}점)</span></div>',
                        unsafe_allow_html=True,
                    )

        st.divider()

        # 등급 카드 (2열 그리드)
        col_a, col_b = st.columns(2)
        for i, r in enumerate(ratings):
            with (col_a if i % 2 == 0 else col_b):
                _render_grade_card(r, changes_map.get(r["stock_id"]))

        st.divider()

        # 요약 테이블
        df = pd.DataFrame([{
            "종목":  r["name"],
            "등급":  f"{GRADE_EMOJI.get(r['grade'],'')} {r['grade']}",
            "점수":  int(r["total_score"]),
            "리스크": int(r["risk_score"]),
            "신뢰도": int(r["data_confidence"]),
            "긍정": r["positive_factors"][0] if r["positive_factors"] else "—",
            "부정": r["negative_factors"][0] if r["negative_factors"] else "—",
        } for r in ratings])
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption(
            f"📅 분석 기준: **{collected_at}** "
            f"| yfinance 전일 종가 · 뉴스 최근 24시간 기준"
        )

    # ── Tab 2: 거시 지표 ──────────────────────────────────────────────────────
    with tab2:
        st.subheader("글로벌 거시 지표")
        if macro:
            _render_macro_panel(macro, collected_at)
        else:
            st.info(
                "💡 '🔄 등급 즉시 새로고침' 또는 '리포트 생성'을 실행하면 "
                "실시간 거시 지표가 표시됩니다."
            )

    # ── Tab 3: 리포트 보기 ────────────────────────────────────────────────────
    with tab3:
        st.subheader("📄 리포트 열람")
        if data.get("report_content"):
            st.markdown(
                f'<div style="font-size:0.82em;color:#6b7280;margin-bottom:8px;">'
                f'방금 생성된 리포트 — <b>{collected_at}</b></div>',
                unsafe_allow_html=True,
            )
            with st.expander("▼ 리포트 내용 펼치기", expanded=True):
                st.markdown(data["report_content"])
            st.divider()

        report_files = _list_report_files()
        if not report_files:
            st.info("저장된 리포트가 없습니다. '리포트 생성 + 저장'을 실행하세요.")
        else:
            st.markdown("**📁 저장된 리포트 목록**")
            sel = st.selectbox("파일 선택", report_files, format_func=lambda p: p.name)
            if sel:
                with st.container():
                    st.markdown(sel.read_text(encoding="utf-8"))

    # ── Tab 4: 종목 상세 ──────────────────────────────────────────────────────
    with tab4:
        st.subheader("종목별 신호 상세")
        st.caption(f"📅 분석 기준: **{collected_at}**")

        sel_name = st.selectbox(
            "종목 선택",
            [r["name"] for r in ratings],
            label_visibility="collapsed",
        )
        r = next((x for x in ratings if x["name"] == sel_name), None)
        if r:
            grade = r["grade"]
            color = GRADE_COLORS[grade]

            # 레이더차트 + 주요 지표
            rc1, rc2 = st.columns([3, 2])
            with rc1:
                _render_radar_chart(r)
            with rc2:
                st.markdown(
                    f'<div style="background:{_BADGE_CSS[grade].split(";")[0].split(":")[1]};'
                    f'border:1.5px solid {color};border-radius:10px;'
                    f'padding:14px 16px;margin-bottom:14px;">'
                    f'<span style="font-weight:800;font-size:1.1em;color:{color};">【{grade}】</span>'
                    f'&nbsp;<span style="font-size:0.85em;color:#374151;">{r["grade_description"]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                mc1, mc2 = st.columns(2)
                mc1.metric("종합 점수",    f"{r['total_score']:.0f} / 100")
                mc2.metric("리스크 점수",  f"{r['risk_score']:.0f} / 100")
                mc1.metric("데이터 신뢰도", f"{r['data_confidence']:.0f} / 100")

                chg = changes_map.get(r["stock_id"])
                if chg and chg["direction"] != "신규":
                    dc = "#15803d" if chg["direction"] == "상승" else (
                         "#b91c1c" if chg["direction"] == "하락" else "#6b7280")
                    di = "📈" if chg["direction"] == "상승" else (
                         "📉" if chg["direction"] == "하락" else "➡️")
                    st.markdown(
                        f'<div style="font-size:0.84em;color:{dc};padding:8px 12px;'
                        f'background:#f8fafc;border-radius:8px;margin-top:8px;">'
                        f'{di} 전일({chg["prev_date"]}) 대비: '
                        f'<b>{chg["prev_grade"]} → {chg["curr_grade"]}</b> '
                        f'({chg["score_delta"]:+.0f}점)</div>',
                        unsafe_allow_html=True,
                    )

            st.divider()

            # 긍정·부정·확인 요인
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                st.markdown("**✅ 긍정 요인**")
                for f in (r["positive_factors"] or ["없음"]):
                    st.markdown(f"- {f}")
            with fc2:
                st.markdown("**⚠️ 부정 요인**")
                for f in (r["negative_factors"] or ["없음"]):
                    st.markdown(f"- {f}")
            with fc3:
                st.markdown("**🔍 확인 필요**")
                for f in (r["check_required"] or ["없음"]):
                    st.markdown(f"- {f}")

            st.divider()

            # 컴포넌트 점수 바차트
            st.markdown("**신호 컴포넌트 점수**")
            comp_df = pd.DataFrame(
                [{"신호": k, "점수": v} for k, v in r["components"].items()]
            )
            st.bar_chart(comp_df.set_index("신호"))
            st.caption(r["disclaimer"])

    # ── Tab 5: 등급 이력 ──────────────────────────────────────────────────────
    with tab5:
        st.subheader("📊 등급 이력 & 변화 추적")
        from app.engine.history_tracker import HistoryTracker as _HT
        tracker   = _HT()
        day_range = st.radio(
            "조회 기간", [7, 14, 30],
            format_func=lambda d: f"최근 {d}일",
            horizontal=True,
        )
        all_hist = tracker.get_all_history(days=day_range)

        # 등급 변화 카드
        if grade_changes:
            changed = [c for c in grade_changes if c["direction"] in ("상승", "하락")]
            if changed:
                st.markdown("#### 최근 등급 변화")
                cols = st.columns(min(len(changed), 4))
                for i, c in enumerate(changed):
                    clr  = "#15803d" if c["direction"] == "상승" else "#b91c1c"
                    icon = "📈" if c["direction"] == "상승" else "📉"
                    with cols[i % 4]:
                        st.markdown(
                            f'<div class="chg-card" style="border-color:{clr};">'
                            f'<div class="chg-name">{icon} {c["name"]}</div>'
                            f'<div class="chg-grade" style="color:{clr};">'
                            f'{c["prev_grade"]} → {c["curr_grade"]}</div>'
                            f'<div class="chg-pts">{c["score_delta"]:+.0f}점</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                st.divider()

        if not all_hist:
            st.info("저장된 이력이 없습니다. 리포트를 생성하면 이력이 누적됩니다.")
        else:
            cfg_obj = _load_config()
            snames  = {s["id"]: s["name"] for s in cfg_obj.watchlist.stocks}

            rows = []
            for entry in all_hist:
                lbl = f"{entry['date']}({'M' if entry.get('report_type')=='morning' else 'E'})"
                for sid, score in entry.get("scores", {}).items():
                    rows.append({
                        "날짜": lbl,
                        "종목": snames.get(sid, sid),
                        "점수": score,
                        "등급": entry.get("grades", {}).get(sid, ""),
                    })

            if rows:
                hdf = pd.DataFrame(rows)
                sel_stocks = st.multiselect(
                    "종목 선택 (최대 6개)",
                    sorted(hdf["종목"].unique()),
                    default=sorted(hdf["종목"].unique())[:6],
                    max_selections=6,
                )
                if sel_stocks:
                    pvt = hdf[hdf["종목"].isin(sel_stocks)].pivot_table(
                        index="날짜", columns="종목", values="점수", aggfunc="mean"
                    ).sort_index()
                    fig = go.Figure()
                    for col in pvt.columns:
                        fig.add_trace(go.Scatter(
                            x=pvt.index, y=pvt[col],
                            mode="lines+markers", name=col,
                            line=dict(width=2),
                            marker=dict(size=6),
                        ))
                    for v, lbl, c in [(75, "추천", "#00C853"), (55, "안전", "#2979FF"),
                                      (35, "보통", "#FF9100"), (15, "주의", "#FF6D00")]:
                        fig.add_hline(
                            y=v, line_dash="dot", line_color=c,
                            annotation_text=lbl, annotation_position="right",
                            annotation_font_color=c,
                        )
                    fig.update_layout(
                        title=dict(text="종목별 점수 추이", font_size=13),
                        yaxis=dict(range=[0, 108]),
                        height=420,
                        margin=dict(l=20, r=80, t=44, b=65),
                        xaxis_tickangle=-30,
                        plot_bgcolor="white",
                        paper_bgcolor="white",
                        legend=dict(orientation="h", y=-0.25),
                    )
                    st.plotly_chart(fig, width="stretch")

            st.markdown("**이력 테이블**")
            trows = []
            for entry in sorted(all_hist, key=lambda x: x["date"], reverse=True):
                for sid, grade in entry.get("grades", {}).items():
                    trows.append({
                        "날짜":  entry["date"],
                        "유형":  "🌅" if entry.get("report_type") == "morning" else "🌙",
                        "종목":  snames.get(sid, sid),
                        "등급":  f"{GRADE_EMOJI.get(grade,'')} {grade}",
                        "점수":  entry.get("scores", {}).get(sid, "—"),
                    })
            if trows:
                st.dataframe(pd.DataFrame(trows), width="stretch", hide_index=True)

    # ── Tab 6: 설정 ───────────────────────────────────────────────────────────
    with tab6:
        st.subheader("⚙️ 설정")

        # 발송 시간 설정
        st.markdown("#### ⏰ 자동 발송 시간")
        st.caption(
            "스케줄러(`스케줄러 실행.bat`)가 이 시간에 리포트를 자동 생성·발송합니다. "
            "변경 후 스케줄러를 재시작해야 적용됩니다."
        )
        sc1, sc2 = st.columns(2)
        with sc1:
            _mh, _mm = map(int, cfg.user.morning_time.split(":"))
            new_morning = st.time_input("🌅 아침 브리핑", value=dtime(_mh, _mm),
                                        help="기본값: 07:00")
        with sc2:
            _eh, _em = map(int, cfg.user.evening_time.split(":"))
            new_evening = st.time_input("🌙 저녁 결산", value=dtime(_eh, _em),
                                        help="기본값: 18:30")
        if st.button("💾 발송 시간 저장", type="primary"):
            nm = new_morning.strftime("%H:%M")
            ne = new_evening.strftime("%H:%M")
            save_report_times(nm, ne)
            _invalidate_config()
            st.success(f"✅ 저장 완료: 🌅 {nm} / 🌙 {ne}")
            st.info("⚠️ 스케줄러가 실행 중이라면 `스케줄러 실행.bat`을 재시작하세요.")
            st.rerun()

        st.divider()

        # 관심종목 & 가중치
        s1, s2 = st.columns(2)
        with s1:
            st.markdown("#### 📋 관심종목")
            st.dataframe(pd.DataFrame([{
                "종목": s["name"],
                "티커": s["ticker"],
                "국가": s["country"],
                "섹터": s["sector"],
                "관심도": "⭐" * s["interest_level"],
            } for s in cfg.watchlist.stocks]), width="stretch", hide_index=True)

        with s2:
            st.markdown("#### ⚖️ 신호 가중치")
            st.dataframe(pd.DataFrame([
                {"신호": k, "가중치": f"{v*100:.0f}%"}
                for k, v in cfg.user.signal_weights.items()
            ]), width="stretch", hide_index=True)

            st.markdown("#### 🎯 등급 임계값")
            st.dataframe(pd.DataFrame([{
                "등급": f"{GRADE_EMOJI.get(g,'')} {g}",
                "범위": f"{v['min_score']}~{v['max_score']}점",
            } for g, v in cfg.user.rating_thresholds.items()]),
                width="stretch", hide_index=True)

        st.divider()
        st.markdown("#### 🎨 관심 테마")
        st.dataframe(pd.DataFrame([{
            "테마": t["name"],
            "관심도": "⭐" * t["interest_level"],
            "거시 민감도": t["macro_sensitivity"],
            "관련 종목 수": len(t["related_stocks"]),
        } for t in cfg.themes.themes]), width="stretch", hide_index=True)

    # ── Tab 7: 종목 관리 ──────────────────────────────────────────────────────
    with tab7:
        _render_watchlist_manager()


if __name__ == "__main__":
    main()
