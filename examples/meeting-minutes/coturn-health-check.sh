#!/bin/bash
# Health check and auto-restart script for coturn
# Run this via cron every 5 minutes

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

LOG_FILE="/var/log/coturn-health.log"

log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Check if coturn is running
if ! systemctl is-active --quiet coturn; then
    log "${RED}❌ Coturn is NOT running${NC}"
    log "${YELLOW}🔄 Attempting to restart coturn...${NC}"
    
    if sudo systemctl restart coturn; then
        log "${GREEN}✅ Coturn restarted successfully${NC}"
        # Send notification (optional)
        # curl -X POST "https://your-webhook-url" -d "Coturn was down and has been restarted"
    else
        log "${RED}❌ Failed to restart coturn${NC}"
        # Send alert (optional)
        # curl -X POST "https://your-webhook-url" -d "CRITICAL: Coturn failed to restart"
        exit 1
    fi
else
    # Coturn is running, check if it's responsive
    if ! sudo netstat -tulpn | grep -q ":3478"; then
        log "${RED}❌ Coturn is running but not listening on port 3478${NC}"
        log "${YELLOW}🔄 Restarting coturn...${NC}"
        sudo systemctl restart coturn
        log "${GREEN}✅ Coturn restarted${NC}"
    else
        log "${GREEN}✅ Coturn is healthy${NC}"
    fi
fi

# Cleanup old logs (keep last 7 days)
find /var/log/coturn-health.log -mtime +7 -delete 2>/dev/null || true
