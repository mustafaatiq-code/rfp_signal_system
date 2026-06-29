@echo off
cd /d "C:\Users\musta\Claude\Projects\Applied Practicum\rfp_signal_system"
python -m streamlit run output/dashboard.py --server.headless true --browser.gatherUsageStats false
