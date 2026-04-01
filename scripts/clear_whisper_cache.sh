#!/bin/bash
set -e

echo "=== Clearing Local Whisper Model Cache ==="

CACHE_DIRS=(
    "$HOME/.cache/whisper"
    "/root/.cache/whisper"
    "/app/.cache/whisper"
    "$HOME/.cache/torch/hub/checkpoints"
    "/var/lib/docker/volumes/ke-ar_whisper_cache/_data"
)

for dir in "${CACHE_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        SIZE=$(du -sh "$dir" 2>/dev/null | cut -f1)
        echo "Deleting $dir ($SIZE)..."
        rm -rf "$dir"/*
    fi
done

echo "=== Cache Cleanup Complete ==="
