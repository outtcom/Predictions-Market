"""30-day test reminder — sends a Telegram alert to evaluate strategy results."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.common.notifier import TelegramNotifier


def main() -> None:
    load_dotenv()

    dashboard_url = os.getenv("GITHUB_PAGES_URL", "https://outtcom.github.io/Predictions-Market/")
    notifier = TelegramNotifier()

    if not notifier.active:
        print("Telegram not configured — reminder skipped.")
        return

    text = (
        f"⏰ <b>30-Day Test Complete!</b>\n\n"
        f"Your $100 paper-trading test has run for 30 days.\n\n"
        f"📊 <b>Review Your Results:</b>\n"
        f'<a href="{dashboard_url}">Open Dashboard</a>\n\n'
        f"📈 <b>Key Metrics to Check:</b>\n"
        f"• Total ROI % (realized P&L / $100)\n"
        f"• Win rate on closed positions\n"
        f"• Sharpe ratio from backtest\n"
        f"• Best signal source (Metaculus vs LLM vs Manifold)\n\n"
        f"🛠️ <b>Next Steps:</b>\n"
        f"• Run: <code>python -m scripts.backtest</code>\n"
        f"• Run: <code>python -m scripts.sweep</code>\n"
        f"• Adjust thresholds based on results\n\n"
        f"Reply here when ready to review — I'll analyze the data with you."
    )

    notifier._send(text)
    print(f"Reminder sent at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
