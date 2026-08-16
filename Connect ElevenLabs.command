#!/bin/bash
# Double-click, paste your ElevenLabs key once, pick a voice from the list, and Excephalon
# speaks in it.
cd "$(dirname "$0")"
".venv/bin/python" -m excephalon.tts_cloud
echo
read -p "Press Enter to close..."
