"""
Email Sender — HTML 이메일 발송 (SMTP)
- 마크다운 → HTML 완전 변환 (테이블·코드 블록·인라인 서식 지원)
- 뉴스 섹션 (긍정·부정 분리, 클릭 링크)
- Gmail SMTP / 앱 비밀번호 방식
"""
from __future__ import annotations

import html as _html
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ── 인라인 마크다운 처리 ──────────────────────────────────────────────────────

def _inline(text: str) -> str:
    """**bold**, `code` 인라인 처리 — HTML 특수문자는 건드리지 않음"""
    # **bold**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # `code`
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#f3f4f6;padding:2px 5px;border-radius:3px;'
        r'font-size:0.88em;font-family:monospace;">\1</code>',
        text,
    )
    return text


# ── 마크다운 테이블 → HTML 테이블 ────────────────────────────────────────────

def _is_separator_row(line: str) -> bool:
    """| :---: | --- | 형태의 구분행 여부"""
    stripped = line.strip().strip("|")
    return bool(re.fullmatch(r"[\s\-:|]+", stripped))


def _parse_table(table_lines: list[str]) -> str:
    """마크다운 테이블 라인들 → HTML <table>"""
    rows: list[tuple[str, list[str]]] = []  # ("header"|"data", [cell, ...])
    header_done = False

    for line in table_lines:
        if _is_separator_row(line):
            header_done = True
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(("header" if not header_done else "data", cells))

    if not rows:
        return ""

    TH = (
        'style="border:1px solid #d1d5db;padding:9px 13px;text-align:left;'
        'background:#f0f4ff;font-weight:700;font-size:0.88em;color:#1e3a8a;'
        'white-space:nowrap;"'
    )
    TD = (
        'style="border:1px solid #d1d5db;padding:8px 13px;text-align:left;'
        'background:#ffffff;font-size:0.88em;color:#374151;"'
    )
    TR_ODD = 'style="background:#f8fafc;"'

    html = [
        '<table style="border-collapse:collapse;width:100%;margin:14px 0;'
        'border:1px solid #d1d5db;border-radius:6px;overflow:hidden;">'
    ]
    for idx, (row_type, cells) in enumerate(rows):
        if row_type == "header":
            html.append("<thead><tr>")
            for cell in cells:
                html.append(f"<th {TH}>{_inline(cell)}</th>")
            html.append("</tr></thead><tbody>")
        else:
            tr_style = TR_ODD if idx % 2 == 0 else ""
            html.append(f"<tr {tr_style}>")
            for cell in cells:
                html.append(f"<td {TD}>{_inline(cell)}</td>")
            html.append("</tr>")
    html.append("</tbody></table>")
    return "".join(html)


# ── 마크다운 → HTML 변환 (메인) ───────────────────────────────────────────────

def _markdown_to_html_body(md: str) -> str:
    """마크다운 본문 → HTML body 내용 (완전 변환)"""
    lines = md.split("\n")
    parts: list[str] = []
    i = 0
    tbody_open = False  # table body 열린 상태 추적

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── 코드 블록 ``` ───────────────────────────────────────────────
        if stripped.startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(
                    lines[i]
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                i += 1
            parts.append(
                '<pre style="background:#1e293b;color:#e2e8f0;padding:14px 16px;'
                'border-radius:7px;font-size:0.82em;font-family:monospace;'
                'overflow-x:auto;margin:14px 0;line-height:1.6;">'
                "<code>" + "\n".join(code_lines) + "</code></pre>"
            )
            i += 1
            continue

        # ── 마크다운 테이블 ─────────────────────────────────────────────
        if stripped.startswith("|"):
            tbl_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl_lines.append(lines[i])
                i += 1
            parts.append(_parse_table(tbl_lines))
            continue

        # ── 가로선 ──────────────────────────────────────────────────────
        if re.fullmatch(r"[-*_]{3,}", stripped):
            parts.append(
                '<hr style="border:none;border-top:1px solid #e5e7eb;margin:18px 0;">'
            )
            i += 1
            continue

        # ── 제목 ────────────────────────────────────────────────────────
        if stripped.startswith("### "):
            parts.append(
                f'<h3 style="color:#374151;font-size:1.0em;font-weight:700;'
                f'margin:16px 0 6px;">{_inline(stripped[4:])}</h3>'
            )
            i += 1
            continue
        if stripped.startswith("## "):
            parts.append(
                f'<h2 style="color:#1e3a8a;font-size:1.12em;font-weight:700;'
                f'margin:20px 0 8px;padding-bottom:5px;'
                f'border-bottom:2px solid #dbeafe;">{_inline(stripped[3:])}</h2>'
            )
            i += 1
            continue
        if stripped.startswith("# "):
            parts.append(
                f'<h1 style="color:#0f172a;font-size:1.4em;font-weight:800;'
                f'margin:0 0 4px;">{_inline(stripped[2:])}</h1>'
            )
            i += 1
            continue

        # ── 인용구 > ────────────────────────────────────────────────────
        if stripped.startswith("> "):
            parts.append(
                f'<blockquote style="background:#f0f9ff;border-left:4px solid #3b82f6;'
                f'padding:10px 14px;margin:10px 0;border-radius:0 6px 6px 0;'
                f'color:#1e40af;font-size:0.92em;">'
                f"{_inline(stripped[2:])}</blockquote>"
            )
            i += 1
            continue

        # ── 비순서 목록 - ───────────────────────────────────────────────
        if stripped.startswith("- "):
            items: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(
                    f'<li style="margin:3px 0;color:#374151;">'
                    f"{_inline(lines[i].strip()[2:])}</li>"
                )
                i += 1
            parts.append(
                f'<ul style="padding-left:20px;margin:6px 0;">{"".join(items)}</ul>'
            )
            continue

        # ── 순서 목록 N. ────────────────────────────────────────────────
        if re.match(r"^\d+\. ", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\. ", lines[i].strip()):
                # 백슬래시 포함 정규식은 f-string 표현식({}) 밖에서 미리 계산
                # (Python 3.11 이하는 f-string 표현식 내 백슬래시를 허용하지 않음)
                item_text = re.sub(r"^\d+\. ", "", lines[i].strip())
                items.append(
                    f'<li style="margin:3px 0;color:#374151;">'
                    f"{_inline(item_text)}</li>"
                )
                i += 1
            parts.append(
                f'<ol style="padding-left:20px;margin:6px 0;">{"".join(items)}</ol>'
            )
            continue

        # ── 빈 줄 ───────────────────────────────────────────────────────
        if not stripped:
            i += 1
            continue

        # ── 일반 텍스트 ─────────────────────────────────────────────────
        parts.append(
            f'<p style="margin:5px 0;line-height:1.65;color:#374151;font-size:0.92em;">'
            f"{_inline(stripped)}</p>"
        )
        i += 1

    return "\n".join(parts)


# ── 뉴스 섹션 HTML ────────────────────────────────────────────────────────────

def _build_news_html(
    news_data: dict[str, list[dict]],
    ratings: list[dict] | None = None,
) -> str:
    """긍정·부정 뉴스 HTML 섹션 생성"""
    if not news_data:
        return ""

    # stock_id → 종목명 매핑
    name_map: dict[str, str] = {}
    if ratings:
        for r in ratings:
            name_map[r["stock_id"]] = r["name"]

    positive: list[dict] = []
    negative: list[dict] = []

    for stock_id, items in news_data.items():
        stock_name = name_map.get(stock_id, stock_id)
        for item in items:
            sentiment = float(item.get("sentiment", 0))
            headline = item.get("headline", "").strip()
            link = item.get("link", "").strip()
            source = item.get("source", "")
            if not headline:
                continue
            entry = {
                "stock_name": stock_name,
                "headline":   headline,
                "link":       link,
                "source":     source,
                "sentiment":  sentiment,
                "_mock":      item.get("_mock", False),
            }
            if sentiment >= 0.3:
                positive.append(entry)
            elif sentiment <= -0.2:
                negative.append(entry)

    # 감성 강도 순 정렬, 중복 헤드라인 제거
    seen: set[str] = set()
    def _dedup(lst: list[dict]) -> list[dict]:
        out = []
        for e in lst:
            key = e["headline"][:60]
            if key not in seen:
                seen.add(key)
                out.append(e)
        return out

    positive = _dedup(sorted(positive, key=lambda x: -x["sentiment"]))[:8]
    negative = _dedup(sorted(negative, key=lambda x: x["sentiment"]))[:5]

    if not positive and not negative:
        return ""

    def _news_row(entry: dict, color: str, bg: str) -> str:
        hl      = entry["headline"]
        lk      = entry["link"]
        nm      = entry["stock_name"]
        sc      = entry["source"]
        is_mock = entry.get("_mock", False)

        # 테스트 데이터 뱃지
        mock_badge = (
            '<span style="background:#fef3c7;color:#92400e;font-size:0.70em;'
            'font-weight:700;border:1px solid #fcd34d;border-radius:3px;'
            'padding:1px 5px;margin-left:5px;">⚠ 테스트 데이터</span>'
        ) if is_mock else ""

        # 헤드라인 링크 (Mock이면 반투명 처리)
        opacity = "opacity:0.65;" if is_mock else ""
        if lk.startswith("http"):
            hl_html = (
                f'<a href="{_html.escape(lk)}" target="_blank" rel="noopener noreferrer" '
                f'style="color:{color};text-decoration:none;font-weight:600;{opacity}">'
                f'{hl} <span style="font-size:0.75em;">↗</span></a>'
                f'{mock_badge}'
            )
        else:
            hl_html = (
                f'<span style="color:{color};font-weight:600;{opacity}">{hl}</span>'
                f'{mock_badge}'
            )

        return (
            f'<tr>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #f3f4f6;'
            f'vertical-align:top;width:80px;">'
            f'<span style="background:{bg};color:{color};font-size:0.75em;'
            f'font-weight:700;padding:2px 7px;border-radius:4px;'
            f'white-space:nowrap;">{nm}</span></td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #f3f4f6;'
            f'font-size:0.88em;line-height:1.5;">{hl_html}'
            f'<span style="color:#9ca3af;font-size:0.78em;margin-left:6px;">'
            f'— {sc}</span></td>'
            f'</tr>'
        )

    html_parts = [
        '<div style="margin-top:28px;border-top:2px solid #e5e7eb;padding-top:20px;">',
        '<h2 style="color:#0f172a;font-size:1.1em;font-weight:800;margin:0 0 16px;">',
        "📰 관련 뉴스</h2>",
    ]

    if positive:
        html_parts += [
            '<div style="margin-bottom:18px;">',
            '<div style="display:flex;align-items:center;margin-bottom:8px;">',
            '<span style="background:#dcfce7;color:#15803d;font-size:0.82em;'
            'font-weight:700;padding:3px 10px;border-radius:4px;">▲ 긍정 뉴스</span>',
            '</div>',
            '<table style="width:100%;border-collapse:collapse;'
            'border:1px solid #d1fae5;border-radius:7px;overflow:hidden;">',
        ]
        for entry in positive:
            html_parts.append(_news_row(entry, "#15803d", "#f0fdf4"))
        html_parts += ["</table></div>"]

    if negative:
        html_parts += [
            '<div style="margin-bottom:18px;">',
            '<div style="display:flex;align-items:center;margin-bottom:8px;">',
            '<span style="background:#fee2e2;color:#b91c1c;font-size:0.82em;'
            'font-weight:700;padding:3px 10px;border-radius:4px;">▼ 부정 뉴스</span>',
            '</div>',
            '<table style="width:100%;border-collapse:collapse;'
            'border:1px solid #fecaca;border-radius:7px;overflow:hidden;">',
        ]
        for entry in negative:
            html_parts.append(_news_row(entry, "#b91c1c", "#fff1f2"))
        html_parts += ["</table></div>"]

    html_parts.append("</div>")
    return "".join(html_parts)


# ── 전체 HTML 이메일 빌드 ─────────────────────────────────────────────────────

def _build_charts_html(chart_images: list[dict] | None) -> str:
    """주목 종목(추천/위험/판단보류·당일 등급 변화) 캔들차트 섹션 — cid: 참조로 인라인 삽입.
    이미지 자체는 send()에서 MIMEImage로 첨부되며, 여기서는 img 태그만 생성한다.
    """
    if not chart_images:
        return ""

    blocks = []
    for item in chart_images:
        sid = item["stock_id"]
        name = _html.escape(item.get("name", sid))
        parts = [
            '<div style="margin:14px 0;padding:14px;background:#f8fafc;'
            'border-radius:10px;border:1px solid #e5e7eb;">'
            f'<div style="font-weight:700;color:#1e3a8a;margin-bottom:8px;">📊 {name}</div>'
        ]
        if item.get("daily"):
            parts.append(
                '<div style="font-size:0.78em;color:#6b7280;margin:6px 0 3px;">일봉(90일)</div>'
                f'<img src="cid:chart_{sid}_daily" width="540" '
                'style="width:100%;max-width:540px;border-radius:6px;" '
                f'alt="{name} 일봉">'
            )
        if item.get("weekly"):
            parts.append(
                '<div style="font-size:0.78em;color:#6b7280;margin:10px 0 3px;">주봉</div>'
                f'<img src="cid:chart_{sid}_weekly" width="540" '
                'style="width:100%;max-width:540px;border-radius:6px;" '
                f'alt="{name} 주봉">'
            )
        parts.append("</div>")
        blocks.append("".join(parts))

    return (
        '<div style="margin-top:20px;padding-top:16px;border-top:1px solid #e5e7eb;">'
        '<div style="font-size:1.05em;font-weight:800;color:#111827;margin-bottom:4px;">📈 주목 종목 차트</div>'
        '<div style="font-size:0.75em;color:#9ca3af;margin-bottom:10px;">'
        '추천·위험·판단보류 등급이거나 당일 등급 변화가 있는 종목만 표시 — 매매 신호가 아닌 참고 자료</div>'
        + "".join(blocks)
        + "</div>"
    )


def _build_html_email(
    body_md: str,
    news_data: dict[str, list[dict]] | None = None,
    ratings: list[dict] | None = None,
    chart_images: list[dict] | None = None,
) -> str:
    """마크다운 본문 + 뉴스 섹션 + 차트 섹션 → 완성된 HTML 이메일"""
    body_html = _markdown_to_html_body(body_md)
    news_html = _build_news_html(news_data or {}, ratings)
    charts_html = _build_charts_html(chart_images)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Flow Intelligence</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#f1f5f9;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;background:#ffffff;
              border-radius:12px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);">

  <!-- 헤더 -->
  <tr>
    <td style="background:linear-gradient(135deg,#1e3a8a 0%,#1d4ed8 100%);
               padding:24px 28px 20px;">
      <div style="color:#ffffff;font-size:1.3em;font-weight:800;
                  letter-spacing:-0.3px;">
        📊 Market Flow Intelligence
      </div>
      <div style="color:#bfdbfe;font-size:0.85em;margin-top:4px;">
        투자 판단 보조 리포트 — {now_str} KST
      </div>
    </td>
  </tr>

  <!-- 본문 -->
  <tr>
    <td style="padding:24px 28px;">
      {body_html}
      {news_html}
      {charts_html}
    </td>
  </tr>

  <!-- 면책 -->
  <tr>
    <td style="background:#f8fafc;padding:16px 28px;
               border-top:1px solid #e5e7eb;">
      <p style="margin:0;font-size:0.75em;color:#9ca3af;line-height:1.6;">
        ⚠️ 본 리포트는 <strong>투자 판단 보조 목적</strong>으로만 제공됩니다.
        투자 결정에 대한 최종 책임은 투자자 본인에게 있으며,
        본 리포트는 특정 종목의 매수·매도를 권유하지 않습니다.<br>
        Market Flow Intelligence — 개인 투자 분석 시스템
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# ── EmailSender 클래스 ────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

import logging as _logging
_logger_es = _logging.getLogger(__name__)


class EmailSender:
    """
    .env 환경변수에서 설정을 읽어 SMTP 이메일을 발송합니다.
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
    """

    def __init__(self) -> None:
        self.host      = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.port      = int(os.getenv("SMTP_PORT", "587"))
        self.user      = os.getenv("SMTP_USER", "")
        self.password  = os.getenv("SMTP_PASSWORD", "")
        self.from_addr = os.getenv("EMAIL_FROM", self.user)
        self.to_addr   = os.getenv("EMAIL_TO", "")

    def is_configured(self) -> bool:
        if not (self.user and self.password and self.to_addr):
            return False
        if not _EMAIL_RE.match(self.to_addr):
            _logger_es.warning("[EMAIL_CONFIG] EMAIL_TO 형식 오류: '%s' — example@gmail.com 형식으로 설정하세요", self.to_addr)
            return False
        return True

    def send(
        self,
        subject: str,
        body_markdown: str,
        news_data: dict[str, list[dict]] | None = None,
        ratings: list[dict] | None = None,
        chart_images: list[dict] | None = None,
    ) -> bool:
        """이메일 발송. True = 성공, False = 실패.
        chart_images: [{"stock_id", "name", "daily": bytes|None, "weekly": bytes|None}, ...]
        """
        if not self.is_configured():
            print("⚠️  이메일 설정 미완료. .env 파일을 확인하세요.")
            return False

        # related(본문+인라인이미지) > alternative(plain/html) 구조 — 이미지 없어도 안전
        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"]    = self.from_addr
        msg["To"]      = self.to_addr

        msg_alt = MIMEMultipart("alternative")
        msg.attach(msg_alt)

        # plain text (폴백)
        msg_alt.attach(MIMEText(body_markdown, "plain", "utf-8"))

        # HTML (완전 렌더링)
        html_content = _build_html_email(body_markdown, news_data, ratings, chart_images)
        msg_alt.attach(MIMEText(html_content, "html", "utf-8"))

        # 캔들차트 인라인 이미지 (Content-ID로 HTML의 cid: 참조와 매칭)
        for item in (chart_images or []):
            sid = item.get("stock_id")
            for kind in ("daily", "weekly"):
                png_bytes = item.get(kind)
                if not png_bytes:
                    continue
                img = MIMEImage(png_bytes, _subtype="png")
                img.add_header("Content-ID", f"<chart_{sid}_{kind}>")
                img.add_header("Content-Disposition", "inline", filename=f"{sid}_{kind}.png")
                msg.attach(img)

        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(self.host, self.port) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(self.user, self.password)
                server.sendmail(self.from_addr, self.to_addr, msg.as_string())
            print(f"✅ 이메일 발송 완료 → {self.to_addr}")
            return True
        except Exception as e:
            print(f"❌ 이메일 발송 실패: {e}")
            return False

    def send_report(
        self,
        report_type: str,
        content: str,
        date_str: str | None = None,
        news_data: dict[str, list[dict]] | None = None,
        ratings: list[dict] | None = None,
        chart_images: list[dict] | None = None,
    ) -> bool:
        date = date_str or datetime.now().strftime("%Y-%m-%d")
        type_label = "아침 브리핑" if report_type == "morning" else "저녁 결산"
        # 제목 형식은 config/report_config.json의 email.subject_* 에서 가져온다.
        # 기존에는 여기서 하드코딩해 config 값이 정의만 되고 무시되고 있었다.
        # 설정 로드나 템플릿 치환이 실패해도 메일 발송 자체는 막지 않는다.
        subject = f"[Market Flow] {date} {type_label}"
        try:
            from app.utils.config_loader import get_config
            key = "subject_morning" if report_type == "morning" else "subject_evening"
            template = get_config().report.email.get(key)
            if template:
                subject = template.format(date=date, type_label=type_label)
        except Exception as e:
            _logger_es.debug("이메일 제목 템플릿 적용 실패, 기본 형식 사용: %s", e)
        return self.send(
            subject, content, news_data=news_data, ratings=ratings, chart_images=chart_images
        )
