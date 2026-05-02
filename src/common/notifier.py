"""Telegram notifier for trade signals and daily summaries."""

from __future__ import annotations

import os
from typing import Any

import requests

from src.common.logger import log_event

TELEGRAM_API = "https://api.telegram.org/bot"


def _escape_html(text: str) -> str:
    """Escape HTML reserved characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramNotifier:
    """Sends trade alerts and summaries to a Telegram chat."""

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self._session = requests.Session()

    @property
    def active(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _send(self, text: str) -> dict[str, Any] | None:
        if not self.active:
            return None
        url = f"{TELEGRAM_API}{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = self._session.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log_event("TELEGRAM_ERROR", {"error": str(exc)})
            return None

    def send_trade_alert(
        self,
        decision: Any,
        market_question: str,
        signal_sources: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Send a formatted trade signal alert.

        `decision` must have attributes: market_id, decision, signal_prob,
        market_price, expected_value, kelly_size_usd, risk_verdict, rationale.
        """
        if not self.active:
            return None

        verdict = decision.risk_verdict
        ev = decision.expected_value

        if verdict == "APPROVED":
            emoji = "🟢" if "BUY" in decision.decision else "⚪"
            header = f"<b>{emoji} TRADE {verdict}</b>"
        else:
            header = f"<b>🔴 TRADE {verdict}</b>"

        q = _escape_html(market_question[:120])
        sources = _escape_html(", ".join(signal_sources or ["unknown"]))
        rationale = _escape_html(decision.rationale[:200])
        market_url = f"https://polymarket.com/event/{decision.market_id}"

        text = (
            f"{header}\n"
            f"📊 <b>{q}</b>\n"
            f"🎯 Signal: <code>{decision.signal_prob:.1%}</code> vs Market: <code>{decision.market_price:.1%}</code>\n"
            f"📈 EV: <code>{ev:.1%}</code> | Size: <code>${decision.kelly_size_usd:.2f}</code>\n"
            f"📎 Sources: {sources}\n"
            f"💡 {rationale}\n"
            f'<a href="{market_url}">Open on Polymarket</a>'
        )

        result = self._send(text)
        log_event("TELEGRAM_SENT", {"type": "trade_alert", "market_id": decision.market_id})
        return result

    def send_daily_summary(
        self,
        open_positions: list[Any],
        closed_today: list[Any],
        bankroll: float,
    ) -> dict[str, Any] | None:
        """Send end-of-cycle portfolio summary."""
        if not self.active:
            return None

        realized_pnl = sum((getattr(p, "pnl_usd", 0) or 0) for p in closed_today)

        wins = [p for p in closed_today if (getattr(p, "pnl_usd", 0) or 0) > 0]
        win_rate = len(wins) / len(closed_today) if closed_today else 0.0

        text = (
            f"📋 <b>Daily Summary</b>\n"
            f"Open positions: <code>{len(open_positions)}</code>\n"
            f"Closed today: <code>{len(closed_today)}</code>\n"
            f"Realized P&amp;L: <code>${realized_pnl:+.2f}</code>\n"
            f"Win rate today: <code>{win_rate:.0%}</code>\n"
            f"Bankroll: <code>${bankroll:.2f}</code>\n\n"
            f"📊 Dashboard (same WiFi):\n"
            f"<code>http://192.168.2.10:8501</code>\n\n"
            f"To start:\n"
            f"<code>start_dashboard.bat</code>"
        )

        result = self._send(text)
        log_event("TELEGRAM_SENT", {"type": "daily_summary"})
        return result
