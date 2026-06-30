#!/bin/bash
# Launcher for the lattice-scanner main bot (canonical live dir).
# Mirrors the old organic-revival-scanner-git tmux loop but points here and
# uses the local venv directly.
cd /home/iradei/lattice-scanner || exit 1
while true; do
  echo "[$(date)] Starting scanner (lattice-scanner)..."
  env/bin/python main.py
  echo "[$(date)] Scanner stopped/crashed, restarting in 5s..."
  sleep 5
done
