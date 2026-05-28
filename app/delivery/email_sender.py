"""
Email Sender — 범용 SMTP 이메일 발송
기본 예시: Gmail SMTP (앱 비밀번호 방식)
교체 방법: .env의 SMTP_HOST/PORT/USER/PASSWORD만 변경하면 다른 SMTP 서버로 전환 가능
"""
from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _markdown_to_html(md: str) -> str:
    """최소한의 마크다운 → HTML 변환 (외부 라이브러리 없이)"""
    lines = md.split("\n")
    html_lines = []
    for line in lines:
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- "):
            html_lines.append(f"<li>{line[2:]}</li>")
        elif line.startswith("> "):
            html_lines.append(f"<blockquote>{line[2:]}</blockquote>")
        elif line.startswith("---"):
            html_lines.append("<hr/>")
        elif line.strip() == "":
            html_lines.append("<br/>")
        else:
            # **bold** 처리
            line = _replace_bold(line)
            html_lines.append(f"<p>{line}</p>")

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 800px; margin: 0 auto; padding: 20px; color: #333; }}
  h1 {{ color: #1a237e; border-bottom: 2px solid #1a237e; padding-bottom: 8px; }}
  h2 {{ color: #283593; margin-top: 24px; }}
  h3 {{ color: #3949ab; }}
  blockquote {{ background: #f3f4f6; border-left: 4px solid #3949ab;
                padding: 12px 16px; margin: 0; }}
  li {{ margin: 4px 0; }}
  hr {{ border: none; border-top: 1px solid #e0e0e0; margin: 20px 0; }}
  p {{ line-height: 1.6; margin: 6px 0; }}
</style>
</head><body>
{"".join(html_lines)}
</body></html>"""


def _replace_bold(text: str) -> str:
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


class EmailSender:
    """
    설정은 전부 .env에서 로드합니다:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
    """

    def __init__(self) -> None:
        self.host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.user = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.from_addr = os.getenv("EMAIL_FROM", self.user)
        self.to_addr = os.getenv("EMAIL_TO", "")

    def is_configured(self) -> bool:
        return bool(self.user and self.password and self.to_addr)

    def send(self, subject: str, body_markdown: str) -> bool:
        """
        Returns True on success, False on failure.
        """
        if not self.is_configured():
            print("⚠️  이메일 설정이 완료되지 않았습니다. .env 파일을 확인해주세요.")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr

        # Plain text fallback
        msg.attach(MIMEText(body_markdown, "plain", "utf-8"))
        # HTML 버전
        msg.attach(MIMEText(_markdown_to_html(body_markdown), "html", "utf-8"))

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

    def send_report(self, report_type: str, content: str, date_str: str | None = None) -> bool:
        date = date_str or datetime.now().strftime("%Y-%m-%d")
        if report_type == "morning":
            subject = f"[Market Flow] {date} 아침 브리핑"
        else:
            subject = f"[Market Flow] {date} 저녁 결산"
        return self.send(subject, content)
