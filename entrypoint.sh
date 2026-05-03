#!/bin/sh
set -e
chown -R botuser:botuser /app/miniapp_dist/covers
exec gosu botuser python main.py
