"""
설정 파일 로더 — config/ 폴더의 JSON 파일을 로드하고 접근 편의를 제공
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 프로젝트 루트는 이 파일 기준 3단계 상위
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _PROJECT_ROOT / "config"


def _load_json(filename: str) -> dict[str, Any]:
    path = _CONFIG_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class WatchlistConfig:
    def __init__(self) -> None:
        data = _load_json("watchlist.json")
        self.stocks: list[dict] = data["stocks"]
        self._by_id: dict[str, dict] = {s["id"]: s for s in self.stocks}

    def get(self, stock_id: str) -> dict | None:
        return self._by_id.get(stock_id)

    def all_ids(self) -> list[str]:
        return list(self._by_id.keys())

    def by_country(self, country: str) -> list[dict]:
        return [s for s in self.stocks if s["country"] == country]

    def by_theme(self, theme: str) -> list[dict]:
        return [s for s in self.stocks if theme in s.get("themes", [])]


class ThemeConfig:
    def __init__(self) -> None:
        data = _load_json("themes.json")
        self.themes: list[dict] = data["themes"]
        self._by_id: dict[str, dict] = {t["id"]: t for t in self.themes}

    def get(self, theme_id: str) -> dict | None:
        return self._by_id.get(theme_id)

    def all_ids(self) -> list[str]:
        return list(self._by_id.keys())


class UserProfile:
    def __init__(self) -> None:
        data = _load_json("user_profile.json")
        self.investor: dict = data["investor"]
        self.report_preferences: dict = data["report_preferences"]
        self.rating_thresholds: dict = data["rating_thresholds"]
        self.signal_weights: dict = data["signal_weights"]
        self.display_order: list[str] = data.get("watchlist_display_order", [])

    @property
    def risk_tolerance(self) -> str:
        return self.investor["risk_tolerance"]

    @property
    def morning_time(self) -> str:
        return self.report_preferences.get("morning_report_time", "07:00")

    @property
    def evening_time(self) -> str:
        return self.report_preferences.get("evening_report_time", "20:30")


class ReportConfig:
    def __init__(self) -> None:
        data = _load_json("report_config.json")
        self.morning: dict = data["morning_report"]
        self.evening: dict = data["evening_report"]
        self.rating_system: dict = data["rating_system"]
        self.email: dict = data["email"]
        self.storage: dict = data["storage"]

    @property
    def disclaimer(self) -> str:
        return self.rating_system["disclaimer"]

    @property
    def forbidden_expressions(self) -> list[str]:
        return self.rating_system["forbidden_expressions"]


class AppConfig:
    """모든 설정을 하나로 묶는 편의 클래스"""

    def __init__(self) -> None:
        self.watchlist = WatchlistConfig()
        self.themes = ThemeConfig()
        self.user = UserProfile()
        self.report = ReportConfig()
        self.project_root = _PROJECT_ROOT

    def report_save_dir(self) -> Path:
        base = os.getenv("REPORT_SAVE_DIR", self.report.storage["base_dir"])
        path = _PROJECT_ROOT / base
        path.mkdir(parents=True, exist_ok=True)
        return path


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def save_report_times(morning_time: str, evening_time: str) -> None:
    """user_profile.json의 리포트 발송 시간을 저장하고 설정 캐시를 초기화합니다."""
    global _config
    path = _CONFIG_DIR / "user_profile.json"
    data = _load_json("user_profile.json")
    data["report_preferences"]["morning_report_time"] = morning_time
    data["report_preferences"]["evening_report_time"] = evening_time
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _config = None  # 캐시 무효화 → 다음 get_config() 호출 시 재로드


def save_watchlist(stocks: list[dict]) -> None:
    """watchlist.json의 종목 목록을 저장하고 설정 캐시를 초기화합니다."""
    global _config
    path = _CONFIG_DIR / "watchlist.json"
    data = _load_json("watchlist.json")
    data["stocks"] = stocks
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _config = None


def save_themes(themes: list[dict]) -> None:
    """themes.json의 테마 목록을 저장하고 설정 캐시를 초기화합니다."""
    global _config
    path = _CONFIG_DIR / "themes.json"
    data = _load_json("themes.json")
    data["themes"] = themes
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _config = None


def save_display_order(order: list[str]) -> None:
    """user_profile.json의 watchlist_display_order를 저장하고 설정 캐시를 초기화합니다."""
    global _config
    path = _CONFIG_DIR / "user_profile.json"
    data = _load_json("user_profile.json")
    data["watchlist_display_order"] = order
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _config = None
