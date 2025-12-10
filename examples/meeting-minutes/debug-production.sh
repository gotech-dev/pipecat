#!/bin/bash
# Production Server Debug Script
# Usage: ./debug-production.sh

echo "=================================="
echo "Meeting Minutes - Production Debug"
echo "Server: Ubuntu 22.04"
echo "Date: $(date)"
echo "=================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
    else
        echo -e "${RED}❌ $2${NC}"
    fi
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

echo "1. Checking TURN Server (coturn)..."
echo "-----------------------------------"
if systemctl is-active --quiet coturn; then
    print_status 0 "Coturn is running"
    echo "Uptime: $(systemctl show coturn --property=ActiveEnterTimestamp | cut -d= -f2)"
else
    print_status 1 "Coturn is NOT running"
    echo "Attempting to restart coturn..."
    sudo systemctl restart coturn
    sleep 2
    if systemctl is-active --quiet coturn; then
        print_status 0 "Coturn restarted successfully"
    else
        print_status 1 "Failed to restart coturn"
        echo "Check logs: sudo journalctl -u coturn -n 50"
    fi
fi

echo ""
echo "2. Checking TURN Ports..."
echo "-----------------------------------"
if sudo netstat -tulpn | grep -q ":3478"; then
    print_status 0 "TURN port 3478 is listening"
    sudo netstat -tulpn | grep ":3478"
else
    print_status 1 "TURN port 3478 is NOT listening"
fi

echo ""
echo "3. Checking Firewall (UFW)..."
echo "-----------------------------------"
if sudo ufw status | grep -q "3478"; then
    print_status 0 "Firewall allows port 3478"
else
    print_warning "Port 3478 not explicitly allowed in firewall"
    echo "Run: sudo ufw allow 3478/udp && sudo ufw allow 3478/tcp"
fi

echo ""
echo "4. Checking Application Process..."
echo "-----------------------------------"
if pgrep -f "bot.py" > /dev/null; then
    print_status 0 "Bot process is running"
    ps aux | grep bot.py | grep -v grep
else
    print_status 1 "Bot process is NOT running"
fi

echo ""
echo "5. Checking System Resources..."
echo "-----------------------------------"
echo "Memory:"
free -h | grep -E "Mem:|Swap:"

echo ""
echo "Disk:"
df -h | grep -E "Filesystem|/$"

echo ""
echo "CPU Load:"
uptime

echo ""
echo "6. Checking Network..."
echo "-----------------------------------"
echo "Public IP:"
curl -s ifconfig.me
echo ""

echo "Active connections on port 3478:"
sudo ss -tunap | grep 3478 | wc -l

echo ""
echo "7. Recent Coturn Logs..."
echo "-----------------------------------"
sudo journalctl -u coturn -n 20 --no-pager | tail -10

echo ""
echo "8. Recent Application Logs (if systemd)..."
echo "-----------------------------------"
if systemctl list-units --type=service | grep -q "meeting-minutes"; then
    sudo journalctl -u meeting-minutes -n 20 --no-pager | tail -10
else
    print_warning "No systemd service found for meeting-minutes"
    echo "Check manual logs in application directory"
fi

echo ""
echo "=================================="
echo "Debug Complete"
echo "=================================="
echo ""
echo "Quick Fixes:"
echo "1. Restart coturn: sudo systemctl restart coturn"
echo "2. Restart app: sudo systemctl restart meeting-minutes"
echo "3. Check detailed logs: sudo journalctl -u coturn -f"
echo ""
