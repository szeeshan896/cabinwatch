#!/usr/bin/env bash
# Starts the agent and brings it back if it ever exits.
cd "$(dirname "$0")/.."
python3 -m pip install -q -r agent/requirements.txt
while true; do
  python3 agent/agent.py "$@"
  echo "Agent stopped. Restarting in 5s (Ctrl-C to stop)."
  sleep 5
done
