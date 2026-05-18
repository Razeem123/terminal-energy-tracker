#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "Creating virtualenv..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install deps
pip install -q -r requirements.txt 2>/dev/null

# Run
python main.py "$@"
