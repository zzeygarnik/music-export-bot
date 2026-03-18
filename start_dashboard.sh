#!/bin/sh
exec streamlit run /app/dashboard.py --server.port=8501 --server.address=0.0.0.0
