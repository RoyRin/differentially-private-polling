#!/bin/bash
cd "$(dirname "$0")"

# Create venv if needed
if [ ! -d "../venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv ../venv
fi

# Install deps
../venv/bin/pip install -q flask pg8000

# Run
echo "Starting server at http://localhost:5001"
../venv/bin/python3 -c "from api.index import app; app.run(debug=True, port=5001)"
