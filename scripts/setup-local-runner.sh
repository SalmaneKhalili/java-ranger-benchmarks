#!/bin/bash
# Setup script for local daily benchexec runner
# Installs a systemd timer that runs benchmarks daily.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

SERVICE_NAME="java-ranger-benchmarks"

# Create the systemd service file
SERVICE_FILE="/home/salmane/.config/systemd/user/${SERVICE_NAME}.service"

mkdir -p "/home/salmane/.config/systemd/user"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Java Ranger Full Benchmark Run (Benchexec)
After=network.target

[Service]
Type=oneshot
ExecStart=${REPO_DIR}/scripts/run-full-benchexec.sh --commit
WorkingDirectory=${REPO_DIR}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

echo "Created service: $SERVICE_FILE"

# Create the systemd timer file
TIMER_FILE="/home/salmane/.config/systemd/user/${SERVICE_NAME}.timer"

cat > "$TIMER_FILE" << EOF
[Unit]
Description=Daily Java Ranger Benchmark Run

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=1h

[Install]
WantedBy=timers.target
EOF

echo "Created timer: $TIMER_FILE"

# Reload and enable
systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}.timer"
systemctl --user start "${SERVICE_NAME}.timer"

echo ""
echo "=== Setup Complete ==="
echo "Service:  ${SERVICE_NAME}.service"
echo "Timer:    ${SERVICE_NAME}.timer"
echo "Status:"
systemctl --user status "${SERVICE_NAME}.timer" --no-pager
echo ""
echo "To view next run: systemctl --user list-timers ${SERVICE_NAME}.timer"
echo "To run manually:  systemctl --user start ${SERVICE_NAME}.service"
echo "To view logs:     journalctl --user -u ${SERVICE_NAME}.service -f"
