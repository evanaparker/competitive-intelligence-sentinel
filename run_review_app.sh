#!/bin/bash
export PYTHONPATH=.deps
exec python3 -m streamlit run review.py --server.port 8501 --server.headless true --global.developmentMode false
