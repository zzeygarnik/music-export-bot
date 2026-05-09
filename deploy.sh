#!/usr/bin/env bash
# Deploy music-export-bot to Aeza VPS
# Usage: ./deploy.sh
set -euo pipefail

SSH="ssh -p 2222 aeza-vps"
REMOTE_DIR="/root/music-export-bot"
IMAGE="music-export-bot:latest"
CONTAINER="music-bot"

echo "[1/6] Pull latest code on server..."
$SSH "cd $REMOTE_DIR && git pull"

echo "[2/6] Remove old Docker image (free RAM before build)..."
$SSH "docker rmi $IMAGE 2>/dev/null || true"

echo "[3/6] Build Docker image..."
$SSH "cd $REMOTE_DIR && docker build -f Dockerfile.prod -t $IMAGE ."

echo "[4/6] Replace container..."
$SSH "docker rm -f $CONTAINER 2>/dev/null || true"
$SSH "docker run -d --name $CONTAINER --network host \
  -v $REMOTE_DIR/yt_cookies.txt:/app/yt_cookies.txt \
  -v $REMOTE_DIR/sc_cookies.txt:/app/sc_cookies.txt \
  -v $REMOTE_DIR/covers:/app/miniapp_dist/covers \
  -v /root/tg-bot-api-data:/var/lib/telegram-bot-api:ro \
  --env-file $REMOTE_DIR/.env \
  $IMAGE"

echo "[5/6] Fix cookie file permissions (botuser needs write access)..."
$SSH "chmod 666 $REMOTE_DIR/yt_cookies.txt $REMOTE_DIR/sc_cookies.txt"

echo "[6/6] Sync miniapp_dist into container..."
$SSH "docker cp $REMOTE_DIR/miniapp_dist/. $CONTAINER:/app/miniapp_dist/"

echo "[6b] Fix miniapp_dist permissions (docker cp sets root:700)..."
$SSH "docker exec $CONTAINER chmod -R 755 /app/miniapp_dist"

echo "Done. Container status:"
$SSH "docker ps --filter name=$CONTAINER --format 'Status: {{.Status}}'"
