#!/bin/bash
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
open "http://127.0.0.1:8082"
# Keep existing V1.10/V1.9 data when upgrading (first launch only)
if [ ! -f "shift.db" ]; then
  OLD_DB=$(find .. -maxdepth 2 -type f \( -path "*shift-v1.10*/*shift.db" -o -path "*shift-v1.9*/*shift.db" \) -print -quit 2>/dev/null)
  if [ -n "$OLD_DB" ]; then
    cp "$OLD_DB" "shift.db"
    echo "Đã giữ nguyên dữ liệu từ bản cũ."
  fi
fi

python3 app.py
