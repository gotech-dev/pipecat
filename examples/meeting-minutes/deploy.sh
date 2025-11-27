#!/bin/bash
# Deploy script for Meeting Minutes Bot
# Tự động pull code, sync dependencies và restart service

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
APP_DIR="/home/admin/pipecat"
SERVICE_DIR="$APP_DIR/examples/meeting-minutes"
SERVICE_NAME="meeting-minutes.service"
PYTHON_VERSION="python3.11"

echo -e "${GREEN}🚀 Starting deployment...${NC}"

# 1. Navigate to app directory
cd "$APP_DIR" || exit 1
echo -e "${YELLOW}📂 Current directory: $(pwd)${NC}"

# 2. Pull latest code
echo -e "${YELLOW}📥 Pulling latest code...${NC}"
if git pull origin main; then
    echo -e "${GREEN}✅ Code updated${NC}"
else
    echo -e "${RED}❌ Failed to pull code${NC}"
    exit 1
fi

# 3. Navigate to service directory
cd "$SERVICE_DIR" || exit 1
echo -e "${YELLOW}📂 Service directory: $(pwd)${NC}"

# 4. Sync dependencies
echo -e "${YELLOW}📦 Syncing dependencies...${NC}"
if /root/.local/bin/uv sync --python $PYTHON_VERSION; then
    echo -e "${GREEN}✅ Dependencies synced${NC}"
else
    echo -e "${RED}❌ Failed to sync dependencies${NC}"
    exit 1
fi

# 5. Restart service
echo -e "${YELLOW}🔄 Restarting service...${NC}"
if sudo systemctl restart $SERVICE_NAME; then
    echo -e "${GREEN}✅ Service restarted${NC}"
else
    echo -e "${RED}❌ Failed to restart service${NC}"
    exit 1
fi

# 6. Wait a bit for service to start
sleep 3

# 7. Check service status
echo -e "${YELLOW}📊 Checking service status...${NC}"
if sudo systemctl is-active --quiet $SERVICE_NAME; then
    echo -e "${GREEN}✅ Service is running${NC}"
    sudo systemctl status $SERVICE_NAME --no-pager -l | head -20
else
    echo -e "${RED}❌ Service is not running${NC}"
    echo -e "${YELLOW}📋 Last 50 lines of logs:${NC}"
    sudo journalctl -u $SERVICE_NAME -n 50 --no-pager
    exit 1
fi

echo -e "${GREEN}🎉 Deployment completed successfully!${NC}"

