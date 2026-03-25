#!/bin/bash
# Entrypoint for CTO listener container
# - Ensures cto user can write to mounted volumes
# - Runs the Python listener as root (for volume writes)
# - The listener uses gosu to drop to 'cto' for claude CLI calls

# Give cto user ownership of data dir (mounted from host as root)
chown -R cto:cto /app/data/logs 2>/dev/null || true
# Ensure cto can read the project
chmod -R a+r /app/project 2>/dev/null || true
# Git safe directory for cto user
su -c "git config --global --add safe.directory /app/project" cto 2>/dev/null || true

# Run the listener (unbuffered for real-time logs)
exec python -u /app/cto_listener.py
