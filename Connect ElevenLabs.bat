@echo off
rem Double-click, paste your ElevenLabs key once, pick a voice from the list, and Excephalon
rem speaks in it. The Mac has "Connect ElevenLabs.command"; a door built for one desk is no door
rem on the other.
"%~dp0.venv\Scripts\python.exe" -m excephalon.tts_cloud
pause
