#!/bin/bash
set -e

echo "=== Setting up onboarding tables ==="
python -c "from setup import setup_onboarding_tables; setup_onboarding_tables()" 2>&1 || echo "WARNING: onboarding setup had issues, continuing..."

echo "=== Starting server ==="
exec python -m uvicorn service:app --host 0.0.0.0 --port 8501
