@echo off
REM Prediction Market Bot — Scheduled Run Wrapper
cd /d "C:\Users\Fahad\OneDrive\Desktop\Claude Projects\Predictions Market"
C:\Users\Fahad\AppData\Local\Programs\Python\Python311\python.exe -m main --mode paper --bankroll 100 --max-pages 2 >> logs\cron.log 2>&1
REM Regenerate static dashboard for GitHub Pages
C:\Users\Fahad\AppData\Local\Programs\Python\Python311\python.exe scripts\generate_static_dashboard.py >> logs\cron.log 2>&1
REM Push updated dashboard to GitHub
git add docs/index.html logs/events.jsonl logs/positions.jsonl data/
git diff --cached --quiet || git commit -m "Update dashboard [auto]"
git push origin master >> logs\cron.log 2>&1
