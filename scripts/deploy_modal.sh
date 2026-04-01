#!/bin/bash
set -e

echo "=== ke-ar Modal Deployment ==="

command -v modal &> /dev/null || pip install modal

echo "Checking Modal authentication..."
modal token info || modal token new

echo "Deploying Modal app 'ke-ar'..."
modal deploy modal_app.py

echo "Preloading Whisper models..."
modal run modal_app.py --action preload --model base
modal run modal_app.py --action preload --model small
modal run modal_app.py --action preload --model large

echo ""
modal run modal_app.py --action list
echo ""
echo "=== Deployment Complete ==="
