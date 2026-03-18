#!/bin/bash
# TrueNAS Init Script — Music Export Bot
# Add this to: System → Init/Shutdown Scripts → Pre Init (type: Script)

APP_DIR="/mnt/Storage/zgrnk_data/music-bot/music-export-bot"

cd "$APP_DIR" || { echo "[start.sh] ERROR: directory not found: $APP_DIR"; exit 1; }

echo "[start.sh] Building image and starting stack..."
docker compose -f docker-compose.prod.yml up -d --build

echo "[start.sh] Done. Dashboard: http://TRUENAS_IP:8501"
