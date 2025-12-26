#!/bin/bash
#
# Cleanup script for orphaned candles
# Removes candle data for tickers that are no longer tracked
#
# Usage:
#   API_KEY=your_key ./cleanup_orphaned_candles.sh
#   API_KEY=your_key API_URL=http://server:8765 ./cleanup_orphaned_candles.sh
#

set -e  # Exit on error
set -u  # Exit on undefined variable
set -o pipefail  # Exit on pipe failures

# Configuration with environment variable overrides
API_URL="${API_URL:-http://localhost:8765}"
API_KEY="${API_KEY:-}"
TIMEOUT="${TIMEOUT:-30}"  # Curl timeout in seconds

# Check if API_KEY is set
if [ -z "$API_KEY" ]; then
    echo "Error: API_KEY environment variable not set"
    echo ""
    echo "Usage:"
    echo "  API_KEY=your_api_key $0"
    echo "  API_KEY=your_api_key API_URL=http://server:8765 $0"
    echo ""
    echo "Environment variables:"
    echo "  API_KEY  - Required: Your API authentication key"
    echo "  API_URL  - Optional: API base URL (default: http://localhost:8765)"
    echo "  TIMEOUT  - Optional: Curl timeout in seconds (default: 30)"
    exit 1
fi

echo "============================================"
echo "Orphaned Candles Cleanup Script"
echo "============================================"
echo ""
echo "Configuration:"
echo "  API URL: $API_URL"
echo "  Timeout: ${TIMEOUT}s"
echo ""

# Test API connectivity
echo "Testing API connectivity..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" \
    -H "X-API-Key: $API_KEY" "$API_URL/health" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "000" ]; then
    echo "Error: Cannot connect to API at $API_URL"
    echo "Please check:"
    echo "  1. The service is running"
    echo "  2. The API_URL is correct"
    echo "  3. Network connectivity"
    exit 1
elif [ "$HTTP_CODE" != "200" ]; then
    echo "Error: API returned HTTP $HTTP_CODE from /health"
    echo "Expected: 200 OK"
    exit 1
fi

echo "✓ API is accessible"
echo ""

# Get current status
echo "Fetching current status..."
STATUS_RESPONSE=$(curl -s -w "\n%{http_code}" --max-time "$TIMEOUT" \
    -H "X-API-Key: $API_KEY" "$API_URL/status" 2>&1)

# Extract HTTP code (last line) and body (everything before)
HTTP_CODE=$(echo "$STATUS_RESPONSE" | tail -n 1)
STATUS=$(echo "$STATUS_RESPONSE" | head -n -1)

if [ -z "$HTTP_CODE" ] || [ "$HTTP_CODE" = "000" ]; then
    echo "Error: Failed to fetch status from $API_URL/status"
    echo "The API may be down or unreachable."
    exit 1
fi

if [ "$HTTP_CODE" != "200" ]; then
    echo "Error: API returned HTTP $HTTP_CODE from /status"
    if [ "$HTTP_CODE" = "401" ]; then
        echo "Authentication failed. Check your API_KEY."
    elif [ "$HTTP_CODE" = "500" ]; then
        echo "Server error. Check API logs for details."
    fi
    echo "Response: $STATUS"
    exit 1
fi

# Validate JSON response
if ! echo "$STATUS" | grep -q "database"; then
    echo "Error: Invalid response from /status endpoint"
    echo "Expected JSON with database field"
    echo "Response: $STATUS"
    exit 1
fi

# Extract metrics using jq (if available)
if command -v jq &> /dev/null; then
    TICKER_COUNT=$(echo "$STATUS" | jq -r '.database.ticker_count')
    TOTAL_CANDLES=$(echo "$STATUS" | jq -r '.database.total_candles')
    CANDLES_PER_TICKER_COUNT=$(echo "$STATUS" | jq -r '.database.candles_per_ticker | length')

    # Validate extracted values
    if ! [[ "$TICKER_COUNT" =~ ^[0-9]+$ ]] || ! [[ "$TOTAL_CANDLES" =~ ^[0-9]+$ ]] || ! [[ "$CANDLES_PER_TICKER_COUNT" =~ ^[0-9]+$ ]]; then
        echo "Error: Failed to parse database metrics from status response"
        echo "Raw response:"
        echo "$STATUS"
        exit 1
    fi

    echo "Current database state:"
    echo "  - Tracked tickers: $TICKER_COUNT"
    echo "  - Total candles in DB: $TOTAL_CANDLES"
    echo "  - Unique tickers with candles: $CANDLES_PER_TICKER_COUNT"
    echo ""

    if [ "$TICKER_COUNT" -eq "$CANDLES_PER_TICKER_COUNT" ]; then
        echo "✓ No orphaned candles detected. Database is clean."
        exit 0
    fi

    ORPHANED_TICKERS=$((CANDLES_PER_TICKER_COUNT - TICKER_COUNT))
    echo "⚠ Detected $ORPHANED_TICKERS tickers with orphaned candles"
    echo ""
else
    echo "⚠ Warning: jq not installed - cannot validate metrics"
    echo "Recommendation: Install jq for better validation (brew install jq / apt-get install jq)"
    echo ""
    echo "Raw database status:"
    if echo "$STATUS" | grep -q "ticker_count"; then
        echo "$STATUS" | grep -E "ticker_count|total_candles|candles_per_ticker" | head -3
    else
        echo "Error: Unable to parse status response"
        echo "$STATUS"
        exit 1
    fi
    echo ""
    echo "Proceeding without validation (not recommended for production)"
    read -p "Continue anyway? (yes/no): " CONTINUE
    if [ "$CONTINUE" != "yes" ]; then
        echo "Cleanup cancelled. Please install jq and try again."
        exit 0
    fi
fi

# Confirm cleanup
read -p "Do you want to clean up orphaned candles? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Cleanup cancelled."
    exit 0
fi

echo ""
echo "Cleaning up orphaned candles..."
CLEANUP_RESPONSE=$(curl -s -w "\n%{http_code}" --max-time "$TIMEOUT" \
    -X POST -H "X-API-Key: $API_KEY" "$API_URL/candles/cleanup" 2>&1)

# Extract HTTP code and body
HTTP_CODE=$(echo "$CLEANUP_RESPONSE" | tail -n 1)
RESULT=$(echo "$CLEANUP_RESPONSE" | head -n -1)

if [ -z "$HTTP_CODE" ] || [ "$HTTP_CODE" = "000" ]; then
    echo "Error: Failed to execute cleanup request"
    echo "The API may be unreachable or the request timed out"
    exit 1
fi

if [ "$HTTP_CODE" != "200" ]; then
    echo "Error: Cleanup failed with HTTP $HTTP_CODE"
    if [ "$HTTP_CODE" = "401" ]; then
        echo "Authentication failed"
    elif [ "$HTTP_CODE" = "500" ]; then
        echo "Server error during cleanup"
    fi
    echo "Response: $RESULT"
    exit 1
fi

if command -v jq &> /dev/null; then
    DELETED=$(echo "$RESULT" | jq -r '.deleted_count')

    # Validate deleted count
    if ! [[ "$DELETED" =~ ^[0-9]+$ ]]; then
        echo "Error: Invalid response from cleanup endpoint"
        echo "Raw response:"
        echo "$RESULT"
        exit 1
    fi

    echo "✓ Cleanup completed: $DELETED candle records deleted"
else
    echo "Cleanup result:"
    if echo "$RESULT" | grep -q "deleted_count"; then
        echo "$RESULT" | grep -E "deleted_count|message"
    else
        echo "Warning: Unexpected response format"
        echo "$RESULT"
    fi
fi

echo ""
echo "Fetching updated status..."
sleep 1

STATUS=$(curl -s -H "X-API-Key: $API_KEY" "$API_URL/status")

if command -v jq &> /dev/null; then
    TICKER_COUNT=$(echo "$STATUS" | jq -r '.database.ticker_count')
    TOTAL_CANDLES=$(echo "$STATUS" | jq -r '.database.total_candles')
    CANDLES_PER_TICKER_COUNT=$(echo "$STATUS" | jq -r '.database.candles_per_ticker | length')

    echo "Updated database state:"
    echo "  - Tracked tickers: $TICKER_COUNT"
    echo "  - Total candles in DB: $TOTAL_CANDLES"
    echo "  - Unique tickers with candles: $CANDLES_PER_TICKER_COUNT"

    if [ "$TICKER_COUNT" -eq "$CANDLES_PER_TICKER_COUNT" ]; then
        echo ""
        echo "✓ Database is now clean!"
    fi
fi

echo ""
echo "============================================"
echo "Cleanup completed successfully"
echo "============================================"
