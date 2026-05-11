#!/usr/bin/env bash
# Start Telegram Local Bot API server
# Requires: TELEGRAM_API_ID, TELEGRAM_API_HASH
# Data is persisted in /root/tg-bot-api-data
set -euo pipefail

TELEGRAM_API_ID="${TELEGRAM_API_ID:?Need TELEGRAM_API_ID}"
TELEGRAM_API_HASH="${TELEGRAM_API_HASH:?Need TELEGRAM_API_HASH}"

docker rm -f tg-bot-api 2>/dev/null || true

docker run -d \
  --name tg-bot-api \
  --network host \
  --restart unless-stopped \
  -v /root/tg-bot-api-data:/var/lib/telegram-bot-api \
  -e TELEGRAM_API_ID="$TELEGRAM_API_ID" \
  -e TELEGRAM_API_HASH="$TELEGRAM_API_HASH" \
  aiogram/telegram-bot-api:latest \
  --local \
  --http-port=8082

echo "tg-bot-api started. Waiting for ready..."
sleep 3
docker logs tg-bot-api --tail 20
