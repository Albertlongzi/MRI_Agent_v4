#!/usr/bin/env bash
# Quick local preview — no backend needed
# Opens http://127.0.0.1:8001
cd "$(dirname "$0")"
python3 -m http.server 8001
