@echo off
rem Double-click to run Excephalon in its window - no terminal needed.
rem A FILE rather than `-m excephalon`, and for the reason launch.pyw's own docstring gives: under
rem pythonw there is no console for a failure to land in, so the launcher has to be something a
rem rename cannot silently invalidate, and something that can put its own reason on screen.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0launch.pyw"
