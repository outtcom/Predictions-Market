@echo off
REM Start Streamlit dashboard accessible from local network (phone on same WiFi)
cd /d "C:\Users\Fahad\OneDrive\Desktop\Claude Projects\Predictions Market"
echo Starting dashboard on http://192.168.2.10:8501
echo ( accessible from any device on your WiFi )
C:\Users\Fahad\AppData\Local\Programs\Python\Python311\python.exe -m streamlit run scripts\dashboard.py --server.address 0.0.0.0 --server.port 8501
