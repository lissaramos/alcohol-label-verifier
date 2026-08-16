#!/usr/bin/env bash
# Runs all three services for local development.
set -e

cleanup() { kill 0; }
trap cleanup EXIT

(cd backend && uvicorn main:app --reload --port 8000) &
(cd extraction-service && uvicorn main:app --reload --port 9000) &
(cd docs && python3 -m http.server 5500) &

wait
