@echo off
rem Double-click, sign in to Google once, and Excephalon's errands can reach Gmail and Calendar.
rem The Mac has "Connect Google.command"; this desk is Windows, and for months it had no door at
rem all - the sign-in expired and the only way back was a command line he does not work from.
"%~dp0.venv\Scripts\python.exe" -m excephalon.google_bridge --connect
pause
