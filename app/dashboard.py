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
from app.engine.rating_analyzer import apply_grade_cap

# ── 타임존 ────────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

def _now_kst() -> datetime:
    return datetime.now(KST)

def _now_et() -> datetime:
    """미국 동부 시간 — 3~10월 EDT(UTC-4), 나머지 EST(UTC-5) 자동 적용"""
    m = datetime.now().month
    offset = -4 if 3 <= m <= 10 else -5
    return datetime.now(timezone(timedelta(hours=offset)))

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
   Market Flow  —  Design System v4
   Personal Finance Intelligence · 2026
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ── 디자인 토큰 ── */
:root {
  --mf-font: "Inter", "Noto Sans KR", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --mf-r-xs: 4px;  --mf-r-sm: 7px;  --mf-r-md: 11px;  --mf-r-lg: 15px;
  --mf-sh-xs: 0 1px 2px rgba(0,0,0,0.05);
  --mf-sh-sm: 0 1px 4px rgba(0,0,0,0.07), 0 1px 2px rgba(0,0,0,0.04);
  --mf-sh-md: 0 4px 16px rgba(0,0,0,0.09), 0 2px 6px rgba(0,0,0,0.05);
  --mf-border: #e5e7eb;  --mf-border-lt: #f3f4f6;
  --mf-bg:    #ffffff;   --mf-surf: #f8fafc;
  --mf-t1:    #0f172a;   --mf-t2: #374151;
  --mf-t3:    #6b7280;   --mf-tm: #9ca3af;
  --mf-green: #15803d;   --mf-red: #b91c1c;
  --mf-orange: #c2410c;  --mf-blue: #1d4ed8;
}

/* ── 전체 폰트 ── */
html, body, [data-testid="stAppViewContainer"],
[data-testid="stSidebar"],
.stMarkdown, .stDataFrame { font-family: var(--mf-font) !important; }

/* ── 기본 레이아웃 ── */
.block-container {
    padding-top: 0.75rem !important;
    padding-bottom: 3rem !important;
    max-width: 1380px !important;
}

/* ── 사이드바 ── */
[data-testid="stSidebar"] { background: #f9fafb !important; }
[data-testid="stSidebar"] > div:first-child { padding-top: 1rem !important; }
[data-testid="stSidebar"] .stButton > button {
    width: 100%; border-radius: var(--mf-r-sm) !important;
    font-weight: 600 !important; font-size: 0.85em !important;
    padding: 0.55rem 1rem !important; margin-bottom: 5px;
    transition: all 0.15s ease !important;
}
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] label, [data-testid="stSidebar"] .stCaption {
    color: #374151 !important;
}

/* ── 메트릭 카드 ── */
[data-testid="stMetric"] {
    background: var(--mf-bg) !important;
    padding: 12px 16px !important;
    border-radius: var(--mf-r-md) !important;
    border: 1px solid var(--mf-border) !important;
    box-shadow: var(--mf-sh-sm) !important;
}
[data-testid="stMetricLabel"] > div,
[data-testid="stMetricLabel"] p {
    color: var(--mf-t3) !important;
    font-size: 0.78em !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em !important;
}
[data-testid="stMetricValue"] > div {
    color: var(--mf-t1) !important;
    font-size: 1.45em !important;
    font-weight: 700 !important;
}
[data-testid="stMetricDelta"] svg { display: inline !important; }
[data-testid="stMetricDeltaIcon-Up"]   { color: var(--mf-green) !important; }
[data-testid="stMetricDeltaIcon-Down"] { color: var(--mf-red)   !important; }

/* ── 구분선 ── */
hr {
    margin: 1rem 0 !important;
    border: none !important;
    border-top: 1px solid var(--mf-border-lt) !important;
}

/* ── 탭 ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0 !important;
    border-bottom: 2px solid var(--mf-border-lt) !important;
}
.stTabs [data-baseweb="tab"] {
    font-size: 0.86em !important;
    font-weight: 500 !important;
    padding: 10px 17px !important;
    border-radius: var(--mf-r-sm) var(--mf-r-sm) 0 0 !important;
    color: var(--mf-t3) !important;
    transition: color 0.15s, background 0.15s !important;
}
.stTabs [aria-selected="true"] {
    color: #1e3a5f !important;
    font-weight: 700 !important;
    background: rgba(30,58,95,0.05) !important;
}

/* ── Alert 박스 ── */
[data-testid="stAlert"] { border-radius: var(--mf-r-md) !important; }
[data-testid="stAlert"] p { color: inherit !important; }

/* ── DataFrame ── */
[data-testid="stDataFrame"] { border-radius: var(--mf-r-md) !important; overflow: hidden; }

/* ━━━━ Hero 헤더 ━━━━ */
.mf-hero {
    background: linear-gradient(118deg, #0a1628 0%, #1a3557 52%, #0d2240 100%);
    border-radius: var(--mf-r-lg);
    padding: 15px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 12px;
    box-shadow: var(--mf-sh-md);
    border: 1px solid rgba(255,255,255,0.06);
}
.mf-title-wrap { display: flex; align-items: center; gap: 10px; }
.mf-logo { font-size: 1.5em; line-height: 1; }
.mf-title {
    font-size: 1.22em;
    font-weight: 800;
    color: #f1f5f9 !important;
    letter-spacing: -0.02em;
    line-height: 1.2;
}
.mf-subtitle {
    font-size: 0.72em;
    color: #64748b !important;
    margin-top: 4px;
    font-weight: 400;
    letter-spacing: 0.01em;
}
.mf-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6px;
}
.mf-datetime {
    font-size: 0.74em;
    color: #475569 !important;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
}
.mf-badges { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.mf-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 11px;
    border-radius: 9999px;
    font-size: 0.72em;
    font-weight: 600;
    white-space: nowrap;
}
.badge-open   { background: rgba(34,197,94,0.15); color: #4ade80; border: 1px solid rgba(74,222,128,0.25); }
.badge-closed { background: rgba(148,163,184,0.10); color: #94a3b8; border: 1px solid rgba(148,163,184,0.20); }

/* ━━━━ 면책 배너 ━━━━ */
.disclaimer {
    background: #fffbeb !important;
    border: 1px solid #fcd34d !important;
    border-radius: var(--mf-r-sm);
    padding: 9px 15px;
    font-size: 0.79em;
    color: #78350f !important;
    margin-bottom: 12px;
    display: flex;
    align-items: flex-start;
    gap: 7px;
    line-height: 1.5;
}

/* ━━━━ 등급 요약 Chips ━━━━ */
.chip-wrap {
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 14px;
}
.chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 5px 14px;
    border-radius: 9999px;
    font-size: 0.81em;
    font-weight: 700;
    white-space: nowrap;
    border: 1.5px solid;
    transition: opacity 0.15s, transform 0.1s;
}
.chip:hover { opacity: 0.82; transform: translateY(-1px); }
.chip-total {
    font-size: 0.74em;
    color: var(--mf-tm) !important;
    font-weight: 500;
    margin-left: 3px;
}

/* ━━━━ 필터 바 ━━━━ */
.filter-row {
    background: var(--mf-surf);
    border: 1px solid var(--mf-border);
    border-radius: var(--mf-r-md);
    padding: 8px 13px;
    margin-bottom: 12px;
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
    font-size: 0.82em;
    color: var(--mf-t3);
}

/* ━━━━ 등급 카드 ━━━━ */
.gc {
    background: var(--mf-bg) !important;
    border-left: 4px solid;
    border-top: 1px solid var(--mf-border-lt);
    border-right: 1px solid var(--mf-border-lt);
    border-bottom: 1px solid var(--mf-border-lt);
    border-radius: 0 var(--mf-r-md) var(--mf-r-md) 0;
    padding: 12px 14px;
    margin-bottom: 9px;
    box-shadow: var(--mf-sh-xs);
    transition: box-shadow 0.15s ease, transform 0.12s ease;
}
.gc:hover {
    box-shadow: var(--mf-sh-md) !important;
    transform: translateY(-2px);
}
.gc-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 5px;
    gap: 8px;
}
.gc-name {
    font-weight: 700;
    font-size: 0.91em;
    color: var(--mf-t1) !important;
    line-height: 1.3;
}
.gc-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 2px;
    flex-shrink: 0;
}
.gc-badge {
    padding: 3px 9px;
    border-radius: var(--mf-r-xs);
    font-size: 0.74em;
    font-weight: 700;
    white-space: nowrap;
    letter-spacing: 0.02em;
}
.gc-ticker {
    font-size: 0.69em;
    color: var(--mf-tm) !important;
    font-variant-numeric: tabular-nums;
}

/* 점수 진행 바 */
.gc-score-bar {
    width: 100%; height: 3px;
    background: var(--mf-border-lt);
    border-radius: 9999px;
    margin: 5px 0 6px;
    overflow: hidden;
}
.gc-score-fill { height: 100%; border-radius: 9999px; }

/* 52주 범위 바 */
.gc-52w-wrap { margin: 3px 0 6px; }
.gc-52w-bar {
    width: 100%; height: 4px;
    background: var(--mf-border-lt);
    border-radius: 9999px;
    position: relative;
    margin: 3px 0 2px;
}
.gc-52w-fill {
    height: 100%; border-radius: 9999px;
    background: linear-gradient(90deg, #93c5fd, #1d4ed8);
}
.gc-52w-dot {
    position: absolute; top: -3px;
    width: 10px; height: 10px;
    border-radius: 50%;
    background: #1d4ed8;
    border: 2px solid white;
    box-shadow: 0 1px 3px rgba(0,0,0,0.25);
    transform: translateX(-50%);
}
.gc-52w-labels {
    display: flex;
    justify-content: space-between;
    font-size: 0.67em;
    color: var(--mf-tm) !important;
}

.gc-nums {
    display: flex;
    gap: 11px;
    font-size: 0.78em;
    margin-bottom: 5px;
    flex-wrap: wrap;
    align-items: center;
}
.gc-score  { font-weight: 700; color: var(--mf-t1) !important; }
.gc-sub    { color: var(--mf-t3) !important; }
.gc-chg-up   { color: var(--mf-green) !important; font-weight: 700; font-size: 0.77em; }
.gc-chg-down { color: var(--mf-red) !important;   font-weight: 700; font-size: 0.77em; }
.gc-pos {
    font-size: 0.77em;
    color: var(--mf-green) !important;
    margin-bottom: 2px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
}
.gc-neg {
    font-size: 0.77em;
    color: var(--mf-red) !important;
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
    color: var(--mf-t3) !important;
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
    margin-bottom: 11px;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--mf-border-lt);
    flex-wrap: wrap;
}
.macro-sec-title { font-size: 0.88em; font-weight: 700; color: var(--mf-t1) !important; }
.macro-badge { padding: 3px 10px; border-radius: 9999px; font-size: 0.73em; font-weight: 600; }
.macro-open   { background: #dcfce7 !important; color: var(--mf-green) !important; }
.macro-closed { background: #f3f4f6 !important; color: var(--mf-t3) !important; }

/* ━━━━ 공포탐욕 카드 ━━━━ */
.fg-card {
    background: var(--mf-bg) !important;
    border: 1px solid var(--mf-border) !important;
    border-radius: var(--mf-r-md);
    padding: 14px 16px;
    text-align: center;
    box-shadow: var(--mf-sh-sm);
}
.fg-lbl { font-size: 0.73em; color: var(--mf-t3) !important; font-weight: 600; margin-bottom: 6px; }
.fg-val { font-size: 2.1em; font-weight: 800; line-height: 1; }
.fg-txt { font-size: 0.78em; color: var(--mf-t3) !important; margin-top: 5px; }

/* ━━━━ 등급 이력 변화 카드 ━━━━ */
.chg-card {
    background: var(--mf-bg) !important;
    border: 1.5px solid;
    padding: 11px 13px;
    border-radius: var(--mf-r-md);
    text-align: center;
    margin-bottom: 4px;
    transition: box-shadow 0.12s;
}
.chg-card:hover { box-shadow: var(--mf-sh-sm); }
.chg-name  { font-weight: 700; font-size: 0.86em; color: var(--mf-t1) !important; margin-bottom: 4px; }
.chg-grade { font-weight: 700; font-size: 0.84em; }
.chg-pts   { font-size: 0.77em; color: var(--mf-t3) !important; margin-top: 3px; }

/* ━━━━ 사이드바 등급 요약 ━━━━ */
.sb-grade-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 3px 0;
    font-size: 0.81em;
    border-bottom: 1px solid var(--mf-border-lt);
}
.sb-grade-row:last-child { border-bottom: none; }
.sb-grade-label { color: var(--mf-t2); font-weight: 500; }
.sb-grade-count { font-weight: 700; min-width: 20px; text-align: right; }

/* ━━━━ 사이드바 데이터 품질 패널 ━━━━ */
.dq-panel {
    background: var(--mf-bg);
    border: 1px solid var(--mf-border);
    border-radius: var(--mf-r-md);
    padding: 11px 13px;
    margin: 6px 0 8px;
}
.dq-label { font-size: 0.72em; font-weight: 700; color: var(--mf-t3); margin-bottom: 5px; text-transform: uppercase; letter-spacing: 0.06em; }
.dq-score { font-size: 1.45em; font-weight: 800; line-height: 1; }
.dq-score-sub { font-size: 0.46em; font-weight: 400; color: var(--mf-tm); margin-left: 3px; }
.dq-bar-wrap { width: 100%; height: 5px; background: var(--mf-border-lt); border-radius: 9999px; margin: 5px 0 3px; }
.dq-bar-fill { height: 100%; border-radius: 9999px; transition: width 0.4s ease; }
.dq-status { font-size: 0.72em; color: var(--mf-t3); }

/* ━━━━ 뉴스 카드 ━━━━ */
.news-card {
    background: var(--mf-surf) !important;
    border: 1px solid var(--mf-border) !important;
    border-radius: var(--mf-r-sm);
    padding: 10px 13px;
    margin-bottom: 7px;
    transition: background 0.12s, box-shadow 0.12s;
}
.news-card:hover {
    background: #eef2f9 !important;
    box-shadow: var(--mf-sh-xs);
}
.news-headline {
    font-size: 0.855em;
    font-weight: 600;
    color: var(--mf-t1) !important;
    line-height: 1.45;
    margin-bottom: 5px;
}
.news-headline a { color: var(--mf-t1) !important; text-decoration: none; }
.news-headline a:hover { color: var(--mf-blue) !important; text-decoration: underline; }
.news-meta {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    font-size: 0.73em;
    color: var(--mf-tm) !important;
}
.news-sent {
    display: inline-block;
    padding: 1px 7px;
    border-radius: var(--mf-r-xs);
    font-weight: 700;
    font-size: 0.87em;
}
.news-theme-tag {
    background: #e8edf4 !important;
    color: #475569 !important;
    padding: 1px 6px;
    border-radius: var(--mf-r-xs);
    font-size: 0.75em;
    margin-right: 2px;
}

/* ━━━━ 등급 카드 — 가격 행 ━━━━ */
.gc-price {
    display: flex;
    gap: 7px;
    align-items: center;
    font-size: 0.80em;
    margin-bottom: 4px;
}
.gc-price-val { color: var(--mf-t2) !important; font-weight: 700; }

/* ━━━━ 시스템 상태 카드 ━━━━ */
.sys-card {
    background: var(--mf-surf) !important;
    border: 1px solid var(--mf-border) !important;
    border-radius: var(--mf-r-md);
    padding: 13px 15px;
    margin-bottom: 10px;
}
.sys-card-title {
    font-size: 0.75em;
    font-weight: 700;
    color: var(--mf-t3) !important;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 7px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--mf-border-lt);
}
.sys-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.81em;
    padding: 4px 0;
    border-bottom: 1px solid var(--mf-border-lt);
}
.sys-row:last-child { border-bottom: none; }
.sys-key  { color: var(--mf-t3) !important; }
.sys-val  { font-weight: 600; color: var(--mf-t1) !important; }
.sys-ok   { color: var(--mf-green) !important; font-weight: 600; }
.sys-warn { color: var(--mf-orange) !important; font-weight: 600; }
.sys-off  { color: var(--mf-tm) !important; }

/* ━━━━ 모바일 대응 ━━━━ */
@media (max-width: 768px) {
    .mf-hero { flex-direction: column; align-items: flex-start; padding: 13px 16px; }
    .mf-right { align-items: flex-start; }
    .mf-badges { justify-content: flex-start; }
    .mf-title  { font-size: 1.08em; }
    .chip-wrap { gap: 5px; }
    .chip { padding: 4px 10px; font-size: 0.77em; }
    .gc { padding: 11px 12px; }
    .gc-name { font-size: 0.87em; }
    .block-container { padding-left: 0.5rem !important; padding-right: 0.5rem !important; }
}
</style>
""", unsafe_allow_html=True)


# ── 등급 색상 팔레트 ──────────────────────────────────────────────────────────
GRADE_COLORS = {
    "추천": "#00C853", "안전": "#2979FF",
    "보통": "#FF9100", "주의": "#FF6D00", "위험": "#D50000",
    "판단보류": "#757575",
}
GRADE_EMOJI = {"추천": "🟢", "안전": "🔵", "보통": "🟡", "주의": "🟠", "위험": "🔴", "판단보류": "🚫"}

_CHIP_CSS = {
    "추천": "background:#f0fdf4;color:#15803d;border-color:#86efac;",
    "안전": "background:#eff6ff;color:#1d4ed8;border-color:#93c5fd;",
    "보통": "background:#fff7ed;color:#c2410c;border-color:#fdba74;",
    "주의": "background:#fff1f0;color:#9a3412;border-color:#fca5a5;",
    "위험": "background:#fef2f2;color:#991b1b;border-color:#f87171;",
    "판단보류": "background:#f3f4f6;color:#374151;border-color:#9ca3af;",
}
_BADGE_CSS = {
    "추천": "background:#f0fdf4;color:#15803d;",
    "안전": "background:#eff6ff;color:#1d4ed8;",
    "보통": "background:#fff7ed;color:#c2410c;",
    "주의": "background:#fff1f0;color:#9a3412;",
    "위험": "background:#fef2f2;color:#991b1b;",
    "판단보류": "background:#f3f4f6;color:#374151;",
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
    d.setdefault("price", {})
    d.setdefault("news", {})
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

    from app.utils.data_validator import DataValidator
    from app.utils.telegram_notifier import TelegramNotifier

    price_data = PriceCollector().collect(stock_ids)
    news_data  = NewsCollector().collect(stock_ids)
    macro_data = MacroCollector().collect()

    # 데이터 품질 검증
    validator    = DataValidator()
    data_quality = validator.validate(price_data, news_data, macro_data, stocks)
    conf            = data_quality["overall"]["confidence"]
    critical_error  = data_quality["overall"].get("critical_data_error", False)
    critical_reasons = data_quality["overall"].get("critical_error_reasons", [])

    # 치명적 오류 시 공포탐욕 지수 산출 보류
    if critical_error:
        sentiment = macro_data.setdefault("sentiment", {})
        sentiment["fear_greed_index"] = {"value": None, "label": "판단보류"}
        sentiment["_fear_greed_suppressed"] = True
        sentiment["_fear_greed_suppressed_reason"] = "지수 데이터 이상 감지"

    scorer  = SignalScorer(weights=cfg.user.signal_weights)
    analyzer = RatingAnalyzer()
    scores  = [scorer.score(s, price_data.get(s["id"], {}), news_data.get(s["id"], []),
                            macro_data, theme_map) for s in stocks]
    ratings = analyzer.analyze_batch(scores, stocks)
    r_dicts = [r.to_dict() for r in ratings]

    order_map = {sid: i for i, sid in enumerate(cfg.user.display_order)}
    r_dicts.sort(key=lambda r: order_map.get(r["stock_id"], 999))

    # data_quality 기반 등급 캡 적용 (치명적 오류 시 전종목 강제 판단보류)
    r_dicts = apply_grade_cap(r_dicts, conf, critical_data_error=critical_error)

    tracker       = HistoryTracker()
    grade_changes = tracker.get_changes(r_dicts, report_type)

    content, saved_path_str, email_result = None, None, None
    date_str = _now_kst().strftime("%Y-%m-%d")

    if save:
        tracker.save_today(
            r_dicts, report_type,
            price_data=price_data,
            news_data=news_data,
            data_quality=data_quality,
        )
        builder = ReportBuilder()
        content = (
            builder.build_morning_report(price_data, news_data, macro_data, r_dicts,
                                         grade_changes=grade_changes,
                                         data_quality=data_quality)
            if report_type == "morning" else
            builder.build_evening_report(price_data, news_data, macro_data, r_dicts,
                                         grade_changes=grade_changes,
                                         data_quality=data_quality)
        )
        saved_path     = _save_report(content, report_type, cfg.report_save_dir())
        saved_path_str = str(saved_path)

        dp = _now_kst().strftime("%Y%m%d")
        jp = cfg.report_save_dir() / f"{dp}_{report_type}_ratings.json"
        # 뉴스 요약: 파일 크기 제한을 위해 종목당 최대 3건만 저장
        news_summary = {k: v[:3] for k, v in news_data.items() if v}
        jp.write_text(
            json.dumps({"date": date_str, "type": report_type,
                        "ratings": r_dicts, "macro": macro_data,
                        "data_quality": data_quality,
                        "price": price_data,
                        "news": news_summary,
                        "collected_at": collected_at},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 텔레그램 알림 (치명적 오류 + 중요 변화)
        notifier = TelegramNotifier()
        if notifier.is_configured():
            if critical_error:
                notifier.notify_critical_data_error(critical_reasons, report_type)
            notifier.notify_grade_changes(grade_changes, data_confidence=conf,
                                          report_type=report_type)

        if send_email:
            sender = EmailSender()
            email_result = (
                ("성공" if sender.send_report(
                    report_type, content, date_str,
                    news_data=news_data, ratings=r_dicts,
                ) else "실패")
                if sender.is_configured() else "설정 미완료"
            )

    return {
        "date": date_str,
        "collected_at": collected_at,
        "type": report_type,
        "ratings": r_dicts,
        "macro": macro_data,
        "price": price_data,
        "news": news_data,
        "data_quality": data_quality,
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
  <div class="mf-title-wrap">
    <span class="mf-logo">📊</span>
    <div>
      <div class="mf-title">Market Flow Intelligence</div>
      <div class="mf-subtitle">AI · 반도체 · HBM · 데이터센터 · 전력 인프라 &nbsp;|&nbsp; 개인 맞춤형 시장 브리핑</div>
    </div>
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
    for grade in ["추천", "안전", "보통", "주의", "위험", "판단보류"]:
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


def _render_grade_card(
    r: dict,
    change: dict | None = None,
    price_info: dict | None = None,
) -> None:
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

    # 등급 캡 표시
    cap_html = ""
    if r.get("grade_capped"):
        raw = r.get("raw_grade", "")
        cap_html = (
            f'&ensp;<span style="font-size:0.70em;background:#fef9c3;color:#92400e;'
            f'border:1px solid #fde68a;border-radius:4px;padding:1px 5px;font-weight:600;">'
            f'⚠️ 원본:{raw}</span>'
        )

    # 가격·등락 행
    price_html = ""
    w52_html   = ""
    if price_info:
        p        = price_info.get("price")
        chg_pct  = price_info.get("change_pct", 0)
        cur      = price_info.get("currency", "")
        is_mock  = price_info.get("_mock", False)
        high_52w = price_info.get("high_52w")
        low_52w  = price_info.get("low_52w")
        if p is not None:
            p_str  = f"₩{p:,.0f}" if cur == "KRW" else f"${p:,.2f}"
            c_clr  = "#15803d" if chg_pct >= 0 else "#b91c1c"
            c_arr  = "▲" if chg_pct >= 0 else "▼"
            m_note = '<span style="color:#9ca3af;font-size:0.70em;margin-left:3px;">[M]</span>' if is_mock else ""
            price_html = (
                f'<div class="gc-price">'
                f'<span class="gc-price-val">{p_str}</span>'
                f'<span style="color:{c_clr};font-size:0.80em;font-weight:700;">'
                f'{c_arr} {abs(chg_pct):.2f}%</span>'
                f'{m_note}</div>'
            )
        # 52주 범위 바
        if p and high_52w and low_52w and (high_52w - low_52w) > 0:
            pct52 = max(0, min(100, (p - low_52w) / (high_52w - low_52w) * 100))
            fmt52 = (lambda v: f"₩{v:,.0f}") if cur == "KRW" else (lambda v: f"${v:.2f}")
            w52_html = (
                f'<div class="gc-52w-wrap">'
                f'<div class="gc-52w-bar">'
                f'<div class="gc-52w-fill" style="width:{pct52:.1f}%;"></div>'
                f'<div class="gc-52w-dot" style="left:{pct52:.1f}%;"></div>'
                f'</div>'
                f'<div class="gc-52w-labels">'
                f'<span>저 {fmt52(low_52w)}</span>'
                f'<span style="color:#6b7280;">52W {pct52:.0f}%</span>'
                f'<span>고 {fmt52(high_52w)}</span>'
                f'</div>'
                f'</div>'
            )

    # 점수 진행 바
    score_pct = min(100, max(0, r["total_score"]))
    score_bar = (
        f'<div class="gc-score-bar">'
        f'<div class="gc-score-fill" style="width:{score_pct:.0f}%;background:{color};opacity:0.75;"></div>'
        f'</div>'
    )

    st.markdown(f"""
<div class="gc" style="border-color:{color};">
  <div class="gc-top">
    <div>
      <div class="gc-name">{emoji} {r['name']}{delta_html}{cap_html}</div>
    </div>
    <div class="gc-right">
      <span class="gc-badge" style="{badgst}">{grade}</span>
      <span class="gc-ticker">{r['ticker']}</span>
    </div>
  </div>
  {price_html}
  <div class="gc-nums">
    <span class="gc-score">점수 {r['total_score']:.0f}</span>
    <span class="gc-sub">리스크 {r['risk_score']:.0f}</span>
    <span class="gc-sub">신뢰도 {r['data_confidence']:.0f}</span>
  </div>
  {score_bar}
  {w52_html}
  <div class="gc-pos">✅ {pos_txt}</div>
  <div class="gc-neg">⚠️ {neg_txt}</div>
</div>""", unsafe_allow_html=True)


def _render_news_section(news_items: list[dict], max_items: int = 5) -> None:
    """뉴스 카드 렌더링 — .news-card CSS 클래스 활용
    - 링크: 실제 URL 있으면 직접 연결, Mock이면 Google News 검색 링크
    - 시각: ISO 형식 → 상대 시간 (N분/시간/일 전)
    """
    if not news_items:
        st.caption("📭 수집된 뉴스가 없습니다.")
        return

    parts = []
    for item in news_items[:max_items]:
        headline  = item.get("headline", "제목 없음")
        link      = (item.get("link") or "").strip()
        sentiment = float(item.get("sentiment", 0))
        source    = item.get("source", "")
        pub_at    = item.get("published_at", "")
        themes    = item.get("themes", [])
        is_mock   = item.get("_mock", False)

        # ── 감성 뱃지 ──
        if sentiment >= 0.2:
            s_style = "color:#15803d;background:#dcfce7;"
            s_label = f"▲ 긍정 {sentiment:+.1f}"
        elif sentiment <= -0.2:
            s_style = "color:#b91c1c;background:#fee2e2;"
            s_label = f"▼ 부정 {sentiment:.1f}"
        else:
            s_style = "color:#6b7280;background:#f3f4f6;"
            s_label = "— 중립"

        # ── 헤드라인 (링크) ──
        # Mock 뉴스: 링크가 있어도 실제 기사 아님 → 반투명 + title 안내
        if link.startswith("http"):
            if is_mock:
                hl_html = (
                    f'<a href="{link}" target="_blank" rel="noopener noreferrer"'
                    f' title="⚠️ 테스트 데이터 — 실제 기사가 아닙니다"'
                    f' style="color:inherit;text-decoration:none;opacity:0.65;">'
                    f'{headline}'
                    f' <span style="font-size:0.75em;color:#d1d5db;">↗</span>'
                    f'</a>'
                )
            else:
                hl_html = (
                    f'<a href="{link}" target="_blank" rel="noopener noreferrer"'
                    f' style="color:inherit;text-decoration:none;">'
                    f'{headline}'
                    f' <span style="font-size:0.78em;color:#9ca3af;font-weight:400;">↗</span>'
                    f'</a>'
                )
        else:
            hl_html = f'<span style="opacity:0.65;">{headline}</span>' if is_mock else headline

        # ── 테마 태그 ──
        theme_html = "".join(
            f'<span class="news-theme-tag">{t}</span>' for t in themes[:3]
        )

        # ── 메타 정보 (Mock 뱃지 우선 표시) ──
        pub_str = _fmt_pub_at(pub_at)
        meta = [f'<span class="news-sent" style="{s_style}">{s_label}</span>']
        if is_mock:
            meta.append(
                '<span style="color:#92400e;background:#fef3c7;font-size:0.70em;'
                'font-weight:700;border:1px solid #fcd34d;border-radius:3px;'
                'padding:1px 5px;letter-spacing:0.3px;" '
                'title="개발/테스트용 가상 데이터입니다. 실제 기사가 아닙니다.">'
                '⚠ 테스트 데이터</span>'
            )
        if source:
            meta.append(f'<span style="font-weight:600;color:#6b7280;">{source}</span>')
        if pub_str:
            meta.append(f'<span>{pub_str}</span>')
        if theme_html:
            meta.append(theme_html)

        parts.append(
            f'<div class="news-card">'
            f'<div class="news-headline">{hl_html}</div>'
            f'<div class="news-meta">{"".join(meta)}</div>'
            f'</div>'
        )

    st.markdown("".join(parts), unsafe_allow_html=True)


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
    keys = [g for g in ["추천", "안전", "보통", "주의", "위험", "판단보류"] if g in dist]
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


def _fmt_pub_at(pub_at: str) -> str:
    """ISO datetime 문자열 → 사용자 친화적 상대 시간 (예: '3시간 전', '5/28 14:30')"""
    if not pub_at:
        return ""
    try:
        dt = datetime.fromisoformat(str(pub_at).replace("Z", ""))
        if dt.tzinfo is not None:           # timezone-aware → naive 로 변환
            dt = dt.replace(tzinfo=None)
        diff_secs = (datetime.now() - dt).total_seconds()
        if diff_secs < 0:                   # 미래 시각 방어
            return dt.strftime("%m/%d %H:%M")
        mins = int(diff_secs / 60)
        if mins < 2:
            return "방금"
        if mins < 60:
            return f"{mins}분 전"
        hours = mins // 60
        if hours < 24:
            return f"{hours}시간 전"
        days = hours // 24
        if days < 7:
            return f"{days}일 전"
        return dt.strftime("%m/%d %H:%M")
    except (ValueError, TypeError):
        s = str(pub_at)
        # ISO 형식에서 날짜+시간만 추출 (YYYY-MM-DDTHH:MM → M/D HH:MM)
        try:
            dt2 = datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
            return dt2.strftime("%m/%d %H:%M")
        except Exception:
            return s[:16] if len(s) >= 16 else s


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
    fg_val = fg.get("value")
    if fg_val is None:
        # 치명적 데이터 오류로 산출이 보류된 경우 (sentiment["fear_greed_index"]["value"]=None)
        fc, fg_val_disp = "#6b7280", "—"
    else:
        fc, fg_val_disp = (
            "#15803d" if fg_val >= 60 else ("#b91c1c" if fg_val <= 30 else "#c2410c")
        ), fg_val
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f'<div class="fg-card">'
            f'<div class="fg-lbl">공포·탐욕 지수</div>'
            f'<div class="fg-val" style="color:{fc};">{fg_val_disp}</div>'
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
        if not row_id or row_id.lower() == "none":   # 신규 종목: ID 자동 생성
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


_EVENT_CATEGORY_LABEL = {
    "macro": "🇺🇸 매크로",
    "policy": "🏛️ 통화정책",
    "disclosure": "📄 공시",
    "filing_deadline": "⚖️ 법정기한",
}


def _render_event_calendar(event_calendar: list[dict]) -> None:
    """향후 이벤트 캘린더 — 이번 주 / 다음 주로 그룹핑해 표시.
    FRED_API_KEY 미설정 시 매크로 지표 항목은 비어 있을 수 있음(다른 3개 소스는 계속 표시됨)."""
    if not event_calendar:
        st.info(
            "💡 표시할 예정 이벤트가 없습니다. `.env`에 `FRED_API_KEY`를 설정하면 "
            "미국 CPI/PPI/고용/소매판매/GDP 발표일이 추가로 표시됩니다."
        )
        return

    today = datetime.now().date()
    week1_end = today + timedelta(days=7)
    groups: dict[str, list[dict]] = {"이번 주": [], "다음 주": [], "그 이후": []}
    for e in event_calendar:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        if d <= week1_end:
            groups["이번 주"].append(e)
        elif d <= week1_end + timedelta(days=7):
            groups["다음 주"].append(e)
        else:
            groups["그 이후"].append(e)

    for label, events in groups.items():
        if not events:
            continue
        st.markdown(f'<p class="sec-lbl">📅 &nbsp;{label}</p>', unsafe_allow_html=True)
        for e in events:
            cat_label = _EVENT_CATEGORY_LABEL.get(e.get("category"), e.get("category", ""))
            st.markdown(
                f'<div class="macro-sec">'
                f'<span class="sys-val" style="font-size:0.82em;color:#6b7280;">{e["date"]}</span>'
                f'<span class="macro-badge macro-closed" style="margin-left:0.5rem;">{cat_label}</span>'
                f'<span style="margin-left:0.6rem;">{e["title"]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.divider()

    st.caption(
        "매크로 지표는 FRED(St. Louis 연준) 공식 API, 통화정책은 FOMC/한국은행 공식 일정, "
        "법정기한은 자본시장법 제출기한 계산값, 공시는 DART 접수 이력(접수일 기준) — "
        "모두 실측 데이터이며 특정 종목의 향후 등락을 예측하는 정보가 아닙니다."
    )


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
        test_email_btn = st.button(
            "📧 이메일 테스트 발송",
            width="stretch",
            help="저장된 최신 리포트를 이메일로 테스트 발송합니다",
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
            _er = st.session_state["last_email"]
            _fn_email = st.success if _er == "성공" else (st.warning if _er == "설정 미완료" else st.error)
            _fn_email(f"📧 이메일: {_er}")
        if "test_email_result" in st.session_state:
            _te = st.session_state["test_email_result"]
            if _te == "성공":
                st.success("📧 테스트 메일 발송 완료!")
            elif _te == "설정 미완료":
                st.warning("⚠️ 이메일 설정이 미완료 상태입니다 (.env 확인)")
            elif _te == "리포트 없음":
                st.warning("⚠️ 발송할 리포트 파일이 없습니다. 먼저 리포트를 생성하세요.")
            else:
                st.error(f"❌ 발송 실패: {_te}")

        # ── 데이터 품질 + 등급 현황 ──
        _dq_sb    = None
        _sb_rats  = []
        if "dashboard_data" in st.session_state:
            _dq_sb   = st.session_state["dashboard_data"].get("data_quality")
            _sb_rats = st.session_state["dashboard_data"].get("ratings", [])

        if _dq_sb:
            _conf_sb  = _dq_sb["overall"]["confidence"]
            _stat_sb  = _dq_sb["overall"]["status"]
            _clr_sb   = "#15803d" if _conf_sb >= 85 else ("#c2410c" if _conf_sb >= 65 else "#b91c1c")
            st.markdown(
                f'<div class="dq-panel">'
                f'<div class="dq-label">📊 데이터 신뢰도</div>'
                f'<div class="dq-score" style="color:{_clr_sb};">{_conf_sb:.0f}'
                f'<span class="dq-score-sub">/ 100</span></div>'
                f'<div class="dq-bar-wrap"><div class="dq-bar-fill" style="width:{_conf_sb:.0f}%;background:{_clr_sb};"></div></div>'
                f'<div class="dq-status">{_stat_sb}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        if _sb_rats:
            _gd_sb = Counter(r["grade"] for r in _sb_rats)
            _rows_sb = []
            for _g, _em in [("추천","🟢"),("안전","🔵"),("보통","🟡"),("주의","🟠"),("위험","🔴"),("판단보류","🚫")]:
                _cnt_sb = _gd_sb.get(_g, 0)
                if _cnt_sb:
                    _gc_sb = GRADE_COLORS[_g]
                    _rows_sb.append(
                        f'<div class="sb-grade-row">'
                        f'<span class="sb-grade-label">{_em} {_g}</span>'
                        f'<span class="sb-grade-count" style="color:{_gc_sb};">{_cnt_sb}</span>'
                        f'</div>'
                    )
            if _rows_sb:
                st.markdown(
                    f'<div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;'
                    f'padding:8px 12px;margin-bottom:6px;">'
                    f'<div style="font-size:0.70em;font-weight:700;color:#6b7280;'
                    f'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:5px;">등급 현황</div>'
                    f'{"".join(_rows_sb)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
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

    if test_email_btn:
        from app.delivery.email_sender import EmailSender
        _es = EmailSender()
        if not _es.is_configured():
            st.session_state["test_email_result"] = "설정 미완료"
        else:
            # 뉴스·등급 데이터: 세션에 있으면 사용
            _cur_data   = st.session_state.get("dashboard_data") or {}
            _test_news  = _cur_data.get("news", {})
            _test_rats  = _cur_data.get("ratings", [])

            # 최신 저장 리포트 사용, 없으면 간단한 테스트 본문
            _report_files = _list_report_files()
            if _report_files:
                _test_content = _report_files[0].read_text(encoding="utf-8")
                _test_subject_type = "morning" if "morning" in _report_files[0].name else "evening"
            else:
                _test_content = (
                    "# 📊 Market Flow — 이메일 테스트\n\n"
                    "이메일 발송 테스트입니다.\n\n"
                    "설정이 정상적으로 완료되었습니다. ✅\n\n"
                    f"발송 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                _test_subject_type = report_type
            with st.spinner("📧 테스트 이메일 발송 중…"):
                _ok = _es.send_report(
                    _test_subject_type,
                    _test_content,
                    date_str=datetime.now().strftime("%Y-%m-%d"),
                    news_data=_test_news or None,
                    ratings=_test_rats or None,
                )
            st.session_state["test_email_result"] = "성공" if _ok else "발송 오류 (SMTP 로그 확인)"
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

    ratings        = data.get("ratings", [])
    macro          = data.get("macro", {})
    event_calendar = data.get("event_calendar", [])
    collected_at   = data.get("collected_at", "—")
    grade_changes  = data.get("grade_changes", [])
    changes_map    = {c["stock_id"]: c for c in grade_changes} if grade_changes else {}

    # ── 탭 ───────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "📈 등급 현황", "🌍 거시 지표", "📄 리포트 보기",
        "🔍 종목 상세", "📊 등급 이력", "⚙️ 설정", "📋 종목 관리", "📅 이벤트 캘린더",
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

        # 데이터 기준일 배너 — 주말·휴장일에 직전 거래일 종가를 "오늘 등락"으로
        # 오해하지 않도록 실제 기준일을 명시 (리포트 프롬프트와 동일한 정보원)
        _fresh = data.get("data_freshness") or {}
        if _fresh.get("latest_data_date"):
            _stale = _fresh.get("stale_days") or 0
            if not _fresh.get("run_is_trading_day"):
                st.warning(
                    f"🗓️ **휴장일 기준 데이터** — 아래 모든 가격·등락률은 "
                    f"**{_fresh['latest_data_date']} 종가** 기준입니다 "
                    f"(주말에는 새로운 거래가 없습니다)."
                )
            elif _stale > 0:
                st.info(
                    f"🗓️ 아래 데이터는 **{_fresh['latest_data_date']} 종가** 기준입니다 "
                    f"(장 시작 전이거나 데이터 반영 지연 — {_stale}일 전)."
                )
            if _fresh.get("mixed_dates"):
                _detail = " · ".join(
                    f"{d} {n}종목" for d, n in sorted(_fresh.get("date_counts", {}).items(), reverse=True)
                )
                st.caption(
                    f"⚠️ 종목별 데이터 기준일이 다릅니다 ({_detail}). "
                    f"기준일이 다른 종목끼리 등락률을 직접 비교하지 마세요."
                )

        # data_quality 경고 배너 (치명적 오류 / 50~70 구간 / 캡 적용 시)
        _dq_now = data.get("data_quality", {}).get("overall", {})
        _dq_conf = _dq_now.get("confidence", 100)
        _critical = _dq_now.get("critical_data_error", False)
        _any_capped = any(r.get("grade_capped") for r in ratings)
        if _critical:
            _critical_reasons = _dq_now.get("critical_error_reasons", [])
            _reasons_md = "\n".join(f"- {r}" for r in _critical_reasons)
            st.error(
                f"🚨 **치명적 데이터 오류 감지 — 시장 판단 보류**  \n"
                "지수·ETF·대형주 데이터 간 모순이 확인되어 전 종목 등급이 **판단보류**로 처리되었습니다.  \n\n"
                f"{_reasons_md}"
            )
        elif _any_capped:
            _capped_names = ", ".join(r["name"] for r in ratings if r.get("grade_capped"))
            st.warning(
                f"⚠️ **데이터 신뢰도 제한 적용** — 일부 종목 등급이 자동 조정되었습니다.  \n"
                f"조정 종목: **{_capped_names}**  \n"
                f"원본 등급은 각 카드의 '원본:xx' 태그 또는 종목 상세 탭에서 확인할 수 있습니다."
            )
        elif any(r.get("data_quality_warning") for r in ratings):
            st.info(
                f"💡 **데이터 신뢰도 보통** ({_dq_conf:.0f}점) — 추가 확인이 필요합니다.  \n"
                "실제 데이터 전환 후 신뢰도가 70점 이상이 되면 이 경고가 사라집니다."
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

        # ── 필터·정렬 컨트롤 ──────────────────────────────────────────────────
        _fc1, _fc2, _fc3 = st.columns([3, 2, 1])
        with _fc1:
            _gf = st.multiselect(
                "등급 필터",
                ["추천", "안전", "보통", "주의", "위험", "판단보류"],
                placeholder="등급 필터 (전체 표시)",
                label_visibility="collapsed",
                key="t1_gf",
            )
        with _fc2:
            _sort_map = {
                "기본 순서": None,
                "점수 높은 순": lambda x: -x["total_score"],
                "점수 낮은 순": lambda x: x["total_score"],
                "리스크 낮은 순": lambda x: x["risk_score"],
                "신뢰도 높은 순": lambda x: -x["data_confidence"],
            }
            _so = st.selectbox(
                "정렬", list(_sort_map.keys()),
                label_visibility="collapsed", key="t1_so",
            )
        # 필터·정렬 적용
        _fr = [r for r in ratings if not _gf or r["grade"] in _gf]
        _sfn = _sort_map.get(_so)
        if _sfn:
            _fr = sorted(_fr, key=_sfn)
        with _fc3:
            _tag = f"**{len(_fr)}** / {len(ratings)}"
            st.markdown(
                f'<div style="padding-top:0.55rem;text-align:right;font-size:0.80em;color:#6b7280;">'
                f'{_tag} 종목</div>',
                unsafe_allow_html=True,
            )

        # 등급 카드 (2열 그리드)
        price_data_t1 = data.get("price", {})
        if not _fr:
            st.info("선택한 등급에 해당하는 종목이 없습니다.")
        else:
            col_a, col_b = st.columns(2)
            for i, r in enumerate(_fr):
                with (col_a if i % 2 == 0 else col_b):
                    _render_grade_card(
                        r,
                        changes_map.get(r["stock_id"]),
                        price_data_t1.get(r["stock_id"]),
                    )

        st.divider()

        # 요약 테이블
        def _price_str(pi: dict | None) -> str:
            if not pi:
                return "—"
            p = pi.get("price")
            if p is None:
                return "—"
            cur = pi.get("currency", "")
            sym = "$" if cur == "USD" else ("£" if cur == "GBP" else ("€" if cur == "EUR" else ""))
            if cur == "KRW":
                return f"₩{int(p):,}"
            if sym:
                return f"{sym}{p:,.2f}"
            return f"{p:,.2f}"

        def _chg_str(pi: dict | None) -> str:
            if not pi:
                return "—"
            c = pi.get("change_pct")
            if c is None:
                return "—"
            arrow = "▲" if c >= 0 else "▼"
            return f"{arrow} {abs(c):.2f}%"

        _price_map_t1 = data.get("price", {})
        df = pd.DataFrame([{
            "종목":   r["name"],
            "등급":   f"{GRADE_EMOJI.get(r['grade'],'')} {r['grade']}",
            "현재가":  _price_str(_price_map_t1.get(r["stock_id"])),
            "등락률":  _chg_str(_price_map_t1.get(r["stock_id"])),
            "점수":   int(r["total_score"]),
            "리스크":  int(r["risk_score"]),
            "신뢰도":  int(r["data_confidence"]),
            "긍정":   r["positive_factors"][0] if r["positive_factors"] else "—",
            "부정":   r["negative_factors"][0] if r["negative_factors"] else "—",
        } for r in ratings])
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption(
            f"📅 분석 기준: **{collected_at}** "
            f"| yfinance 전일 종가 · 뉴스 최근 24시간 기준"
        )

        # ── 뉴스 개요 (접을 수 있는 섹션) ──────────────────────────────────────
        news_all = data.get("news", {})
        if news_all:
            st.divider()
            with st.expander("📰 최신 뉴스 요약 (클릭해서 펼치기)", expanded=False):
                # 주목 등급(판단보류·추천·위험) 먼저, 나머지는 등급 순
                grade_order = {"판단보류": 0, "추천": 1, "위험": 2, "주의": 3, "보통": 4, "안전": 5}
                sorted_r = sorted(
                    ratings,
                    key=lambda x: grade_order.get(x["grade"], 9),
                )
                shown = 0
                for r_n in sorted_r:
                    items_n = news_all.get(r_n["stock_id"], [])
                    if not items_n:
                        continue
                    if shown > 0:
                        st.markdown("<hr style='margin:6px 0;border-color:#f0f0f0;'>",
                                    unsafe_allow_html=True)
                    emoji_n = GRADE_EMOJI.get(r_n["grade"], "")
                    badge_n = _BADGE_CSS.get(r_n["grade"], "")
                    st.markdown(
                        f'<span style="{badge_n}font-size:0.82em;font-weight:700;'
                        f'padding:2px 9px;border-radius:5px;">'
                        f'{emoji_n} {r_n["name"]}</span>',
                        unsafe_allow_html=True,
                    )
                    _render_news_section(items_n, max_items=2)
                    shown += 1

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
            _rc_hdr, _rc_dl = st.columns([3, 1])
            with _rc_hdr:
                st.markdown(
                    f'<div style="font-size:0.82em;color:#6b7280;padding-top:0.4rem;">'
                    f'방금 생성된 리포트 — <b>{collected_at}</b></div>',
                    unsafe_allow_html=True,
                )
            with _rc_dl:
                _rc_fname = f"{data.get('date','report')}_{data.get('type','morning')}.md"
                st.download_button(
                    "⬇️ 다운로드",
                    data=data["report_content"].encode("utf-8"),
                    file_name=_rc_fname,
                    mime="text/markdown",
                    use_container_width=True,
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
                content_txt = sel.read_text(encoding="utf-8")
                # 다운로드 버튼
                dl_col, _ = st.columns([1, 3])
                with dl_col:
                    st.download_button(
                        label="⬇️ Markdown 다운로드",
                        data=content_txt.encode("utf-8"),
                        file_name=sel.name,
                        mime="text/markdown",
                    )
                with st.container():
                    st.markdown(content_txt)

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

                # 가격 정보 (상세)
                _pi = data.get("price", {}).get(r["stock_id"])
                if _pi:
                    _p    = _pi.get("price")
                    _chg  = _pi.get("change_pct", 0)
                    _cur  = _pi.get("currency", "")
                    _vol  = _pi.get("volume_ratio")
                    if _p is not None:
                        _p_str = f"₩{_p:,.0f}" if _cur == "KRW" else f"${_p:,.2f}"
                        _c_clr = "#15803d" if _chg >= 0 else "#b91c1c"
                        _c_arr = "▲" if _chg >= 0 else "▼"
                        _vol_s = f"거래량 {_vol:.1f}배" if _vol else ""
                        mc2.metric(
                            "현재가",
                            _p_str,
                            f"{_c_arr} {abs(_chg):.2f}%",
                        )
                        if _vol_s:
                            mc1.caption(_vol_s)

                # raw_grade / final_grade 비교 (캡 적용 시)
                if r.get("grade_capped"):
                    st.markdown(
                        f'<div style="background:#fef9c3;border:1.5px solid #fde68a;'
                        f'border-radius:9px;padding:10px 14px;margin-top:10px;font-size:0.83em;">'
                        f'⚠️ <b>데이터 신뢰도 제한 적용</b><br>'
                        f'원본 등급: <b>{r["raw_grade"]}</b> &nbsp;→&nbsp; '
                        f'표시 등급: <b>{r["grade"]}</b><br>'
                        f'<span style="color:#92400e;">{r.get("cap_reason","")}</span><br>'
                        f'신뢰도 점수: {r.get("data_quality_score", "?"):.0f}점'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

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

            # ── 지지/저항 · 손익비 (예측 아님 — 조건부 참고) ────────────────────
            _sr = (_pi or {}).get("support_resistance") or {}
            if _sr and (_sr.get("resistance_zones") or _sr.get("support_zones")):
                st.divider()
                st.markdown(
                    '**📐 지지/저항 · 손익비** '
                    '<span style="font-size:0.75em;color:#9ca3af;">(예측 아님 — 조건부 참고)</span>',
                    unsafe_allow_html=True,
                )
                _cur2    = (_pi or {}).get("currency", "")
                _sym2    = "₩" if _cur2 == "KRW" else "$"
                _r_zones = _sr.get("resistance_zones") or []
                _s_zones = _sr.get("support_zones") or []
                _rr      = _sr.get("risk_reward_ratio")
                _rr_ok   = _sr.get("risk_reward_meets_bar")

                src1, src2, src3 = st.columns(3)
                if _r_zones:
                    rz = _r_zones[0]
                    src1.metric(
                        "저항까지",
                        f"+{_sr.get('nearest_resistance_pct', 0):.1f}%",
                        f"{_sym2}{rz['low']:,.0f}~{rz['high']:,.0f} (강도{rz['strength']})",
                        delta_color="off",
                    )
                else:
                    src1.metric("저항까지", "확인 안 됨", "신고가 구간")

                if _s_zones:
                    sz = _s_zones[0]
                    src2.metric(
                        "지지까지",
                        f"-{_sr.get('nearest_support_pct', 0):.1f}%",
                        f"{_sym2}{sz['low']:,.0f}~{sz['high']:,.0f} (강도{sz['strength']})",
                        delta_color="off",
                    )
                else:
                    src2.metric("지지까지", "확인 안 됨", "")

                if _rr is not None:
                    _rr_clr = "#15803d" if _rr_ok else "#b91c1c"
                    _rr_lbl = "기준충족" if _rr_ok else "기준미달"
                    src3.markdown(
                        f'<div style="padding-top:2px;">'
                        f'<div style="font-size:0.8em;color:#6b7280;">손익비</div>'
                        f'<div style="font-size:1.4em;font-weight:700;color:{_rr_clr};">{_rr:.2f}</div>'
                        f'<div style="font-size:0.78em;color:{_rr_clr};">{_rr_lbl} (기준 2.0)</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    src3.metric("손익비", "산출 불가", "")

                _scenario_lines = []
                if _r_zones:
                    _scenario_lines.append(
                        f"▲ {_sym2}{_r_zones[0]['low']:,.0f} 위 거래량 동반 종가 마감 → 돌파로 볼 여지"
                    )
                if _s_zones:
                    _scenario_lines.append(
                        f"▼ {_sym2}{_s_zones[0]['high']:,.0f} 아래 거래량 동반 종가 이탈 → 지지 붕괴로 볼 여지"
                    )
                if _scenario_lines:
                    st.markdown(
                        '<div style="background:#f8fafc;border-radius:8px;padding:10px 14px;'
                        'margin-top:8px;font-size:0.82em;color:#374151;">'
                        + "<br>".join(_scenario_lines)
                        + "</div>",
                        unsafe_allow_html=True,
                    )

            # ── 수급 동향 (외국인/기관/개인 추정 순매매, KR 종목만) ────────────
            _flow = (_pi or {}).get("investor_flow") or {}
            if _flow:
                st.divider()
                st.markdown(
                    '**💹 수급 동향** '
                    '<span style="font-size:0.75em;color:#9ca3af;">'
                    '(참고용 — 개인은 외국인·기관 합산 잔차 추정치)</span>',
                    unsafe_allow_html=True,
                )
                fc_a, fc_b = st.columns(2)
                for _col, _days in ((fc_a, 5), (fc_b, 20)):
                    _frgn  = _flow.get(f"foreign_net_{_days}d")
                    _inst  = _flow.get(f"institution_net_{_days}d")
                    _indiv = _flow.get(f"individual_net_{_days}d_est")
                    if _frgn is None:
                        continue
                    _col.markdown(f"**{_days}일 누적**")
                    _col.markdown(
                        f"외국인 {'🔵' if _frgn >= 0 else '🔴'} {_frgn:+,}주  \n"
                        f"기관 {'🔵' if _inst >= 0 else '🔴'} {_inst:+,}주  \n"
                        f"개인(추정) {'🔵' if _indiv >= 0 else '🔴'} {_indiv:+,}주"
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

            # ── 뉴스 섹션 ──────────────────────────────────────────────────────
            st.divider()
            st.markdown("**📰 관련 뉴스**")
            _stock_news = data.get("news", {}).get(r["stock_id"], [])
            _render_news_section(_stock_news, max_items=5)

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

        # ── 데이터 품질 추이 차트 ──────────────────────────────────────────────
        st.markdown("#### 📊 데이터 품질 신뢰도 추이")
        qrows = []
        for entry in all_hist:
            q_conf = entry.get("data_quality", {}).get("overall", {}).get("confidence")
            if q_conf is not None:
                qrows.append({
                    "날짜": f"{entry['date']}({'M' if entry.get('report_type')=='morning' else 'E'})",
                    "신뢰도": q_conf,
                })
        if len(qrows) < 2:
            st.caption("📭 누적 데이터 부족 — 리포트를 2회 이상 생성하면 추이가 표시됩니다.")
        else:
            qdf = pd.DataFrame(qrows)
            fig_q = go.Figure()
            fig_q.add_trace(go.Scatter(
                x=qdf["날짜"], y=qdf["신뢰도"],
                mode="lines+markers",
                line=dict(color="#2979FF", width=2),
                marker=dict(size=7),
                name="데이터 신뢰도",
                hovertemplate="%{x}<br>신뢰도: %{y:.0f}점<extra></extra>",
            ))
            # 기준선
            for yv, lbl, clr in [(70, "정상(70)", "#15803d"), (50, "경고(50)", "#c2410c"), (30, "제한(30)", "#b91c1c")]:
                fig_q.add_hline(
                    y=yv, line_dash="dot", line_color=clr, line_width=1.2,
                    annotation_text=lbl, annotation_position="right",
                    annotation_font_color=clr, annotation_font_size=10,
                )
            fig_q.update_layout(
                title=dict(text="데이터 품질 신뢰도 추이 (0~100점)", font_size=13),
                yaxis=dict(range=[0, 108]),
                height=280,
                margin=dict(l=10, r=80, t=44, b=50),
                xaxis_tickangle=-30,
                plot_bgcolor="white",
                paper_bgcolor="white",
                showlegend=False,
            )
            st.plotly_chart(fig_q, width="stretch")
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
                    raw_g  = entry.get("raw_grades", {}).get(sid, grade)
                    capped = entry.get("grade_capped", {}).get(sid, False)
                    trows.append({
                        "날짜":    entry["date"],
                        "유형":    "🌅" if entry.get("report_type") == "morning" else "🌙",
                        "종목":    snames.get(sid, sid),
                        "표시등급": f"{GRADE_EMOJI.get(grade,'')} {grade}",
                        "원본등급": f"{GRADE_EMOJI.get(raw_g,'')} {raw_g}" if capped else "—",
                        "캡적용":  "⚠️" if capped else "",
                        "점수":    entry.get("scores", {}).get(sid, "—"),
                        "신뢰도":  entry.get("data_quality", {}).get("overall", {}).get("confidence", "—"),
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
                                        help="기본값: 20:30")
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

        st.divider()

        # ── 시스템 상태 패널 ───────────────────────────────────────────────────
        st.markdown("#### 🖥️ 시스템 상태")

        # 각 상태값 수집
        _mock_mode  = _is_mock_mode()
        _py_ver     = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        _last_run   = st.session_state.get("last_run", "—")
        _report_dir = cfg.report_save_dir()
        _report_cnt = len(list(_report_dir.glob("*.md"))) if _report_dir.exists() else 0
        _log_path   = _PROJECT_ROOT / "data" / "logs" / "market_flow.log"
        _log_ok     = _log_path.exists()
        _tg_tok     = bool(os.getenv("TELEGRAM_BOT_TOKEN", ""))
        _tg_cid     = bool(os.getenv("TELEGRAM_CHAT_ID", ""))
        _email_cfg  = bool(os.getenv("EMAIL_SENDER", "") and os.getenv("EMAIL_PASSWORD", ""))
        _api_key    = bool(os.getenv("ANTHROPIC_API_KEY", ""))

        def _s(ok: bool, ok_txt: str, no_txt: str) -> str:
            cls = "sys-ok" if ok else "sys-warn"
            return f'<span class="{cls}">{"✅ " + ok_txt if ok else "⚠️ " + no_txt}</span>'

        def _o(val) -> str:
            return f'<span class="sys-val">{val}</span>'

        ss1, ss2 = st.columns(2)
        with ss1:
            st.markdown(f"""
<div class="sys-card">
  <div class="sys-card-title">🔧 실행 환경</div>
  <div class="sys-row"><span class="sys-key">Python</span>{_o(_py_ver)}</div>
  <div class="sys-row"><span class="sys-key">데이터 모드</span>
    {'<span class="sys-warn">⚠️ Mock</span>' if _mock_mode else '<span class="sys-ok">✅ 실시간</span>'}
  </div>
  <div class="sys-row"><span class="sys-key">마지막 실행</span>
    <span class="sys-val" style="font-size:0.79em;">{_last_run}</span>
  </div>
  <div class="sys-row"><span class="sys-key">저장된 리포트</span>{_o(f"{_report_cnt}개")}</div>
  <div class="sys-row"><span class="sys-key">로그 파일</span>
    {_s(_log_ok, "활성", "미생성 (첫 실행 후 생성)")}
  </div>
</div>""", unsafe_allow_html=True)

        with ss2:
            st.markdown(f"""
<div class="sys-card">
  <div class="sys-card-title">🔑 API · 알림 설정</div>
  <div class="sys-row"><span class="sys-key">Claude API</span>
    {_s(_api_key, "키 설정됨", ".env ANTHROPIC_API_KEY 미설정")}
  </div>
  <div class="sys-row"><span class="sys-key">텔레그램 봇</span>
    {_s(_tg_tok and _tg_cid, "설정 완료", "미설정 (선택 사항)")}
  </div>
  <div class="sys-row"><span class="sys-key">이메일 발송</span>
    {_s(_email_cfg, "설정 완료", "미설정 (선택 사항)")}
  </div>
  <div class="sys-row"><span class="sys-key">리포트 저장 경로</span>
    <span class="sys-val" style="font-size:0.75em;">{str(_report_dir)}</span>
  </div>
</div>""", unsafe_allow_html=True)

        if _mock_mode:
            st.info(
                "💡 **Mock 모드 실행 중** — 실제 주가·뉴스 데이터가 아닌 임의 데이터입니다.  \n"
                "실시간 데이터 전환: `.env` 파일에서 `USE_MOCK_DATA=false` 설정 후 재시작"
            )

    # ── Tab 7: 종목 관리 ──────────────────────────────────────────────────────
    with tab7:
        _render_watchlist_manager()

    # ── Tab 8: 이벤트 캘린더 ──────────────────────────────────────────────────
    with tab8:
        st.subheader("📅 예정 이벤트 캘린더 (향후 2주)")
        _render_event_calendar(event_calendar)


if __name__ == "__main__":
    main()
