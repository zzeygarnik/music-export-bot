#!/bin/sh
# Starts the Streamlit dashboard in the background, then the bot in the foreground.
# Use this as the entrypoint for a unified TrueNAS Custom App (bot + dashboard in one container).
streamlit run /app/dashboard.py --server.port=8501 --server.address=0.0.0.0 &
exec python /app/main.py
