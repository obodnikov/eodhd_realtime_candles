# Admin Web UI Documentation

This document provides comprehensive documentation for the Flask-based admin web interface included in the EODHD Real-Time Candle Aggregator v0.4.0.

---

## Overview

The Admin Web UI is a professional web interface designed with the **sqowe brand guidelines** that provides:

- 📊 **Real-time Dashboard**: Monitor system status, WebSocket connections, and database statistics
- 🎯 **Ticker Management**: Add, remove, and monitor tracked tickers with a visual interface
- 📈 **Candle Data Viewer**: Browse and visualize OHLCV candle data with interactive Chart.js charts
- ⚙️ **Configuration Management**: Update service configuration in real-time via web forms

---

## Quick Start

### 1. Access the Admin UI

By default, the admin UI runs on port **5000** and is accessible only from **localhost**:

```bash
# Open in your browser
http://localhost:5000
```

### 2. Login

Use the same `API_KEY` from your `.env` file to login:

1. Navigate to `http://localhost:5000`
2. Enter your API key
3. Click "Sign In"

The session will remain active until you log out or the session expires.

---

## Configuration

### Environment Variables

Configure the admin UI using these environment variables in your `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_ENABLED` | `true` | Enable/disable the admin web interface |
| `ADMIN_HOST` | `127.0.0.1` | Host to bind the admin UI (see Security section) |
| `ADMIN_PORT` | `5000` | Port for the admin UI |
| `API_KEY` | (required) | Same API key used for main REST API |

### Example Configuration

```bash
# .env file
API_KEY=your_secret_api_key_here

# Admin UI Configuration
ADMIN_ENABLED=true
ADMIN_HOST=127.0.0.1  # Localhost only (secure)
ADMIN_PORT=5000
```

---

## Security

### Access Control

**Default Configuration (Recommended):**
- `ADMIN_HOST=127.0.0.1` - Admin UI accessible **only from localhost**
- Ideal for Docker containers running on the same host
- Most secure option for production environments

**External Access Configuration:**
- `ADMIN_HOST=0.0.0.0` - Admin UI accessible from **any network interface**
- ⚠️ **Warning**: Only use this if you have additional security measures in place:
  - Reverse proxy with HTTPS (nginx, Traefik)
  - VPN or SSH tunnel for remote access
  - Firewall rules limiting access to specific IPs

### Authentication

- Single API key authentication using the same `API_KEY` from `.env`
- Session-based authentication with secure cookies
- Sessions persist across page reloads
- Automatic session timeout (configurable via Flask session settings)

### Accessing from Remote Machines

**Option 1: SSH Tunnel (Recommended)**

Create an SSH tunnel to securely access the admin UI:

```bash
# On your local machine
ssh -L 5000:localhost:5000 user@docker-host

# Then access http://localhost:5000 in your browser
```

**Option 2: Reverse Proxy with HTTPS**

Configure nginx or similar:

```nginx
server {
    listen 443 ssl;
    server_name admin.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Features

### 1. Dashboard

**URL**: `http://localhost:5000/dashboard`

The dashboard provides real-time monitoring of:

**WebSocket Status:**
- Connection state (Connected/Disconnected)
- Number of active subscriptions
- Connection timestamp
- Reconnect button for manual reconnection

**Database Statistics:**
- Total candles stored
- Number of tracked tickers
- Oldest and newest candle timestamps

**Configuration Overview:**
- Current candle interval
- Max candles per ticker
- Max ticker limit
- WebSocket reconnect delay

**Active Candles:**
- List of tickers with candles currently being built
- Real-time tick counts

### 2. Ticker Management

**URL**: `http://localhost:5000/tickers`

**Add Tickers:**
- Enter comma-separated ticker symbols (e.g., `AAPL, MSFT, GOOGL`)
- Symbols are automatically converted to uppercase
- Visual feedback on success/error

**View Tickers:**
- Table showing all tracked tickers
- Status indicators (active/inactive)
- Added timestamp
- Last tick received
- Last price
- Candle count

**Remove Tickers:**
- Remove individual tickers with delete button
- Select multiple tickers with checkboxes
- Bulk remove with "Remove Selected" button
- Confirmation dialogs prevent accidental deletion

### 3. Candle Data Viewer

**URL**: `http://localhost:5000/candles`

**Select Ticker:**
- Dropdown list of all tracked tickers
- Automatically loads data when ticker is selected

**Interactive Chart:**
- Chart.js powered OHLCV visualization
- Shows Close, High, and Low prices
- Hover tooltips with detailed price information
- Responsive design adapts to screen size
- sqowe brand colors for consistent design

**Data Table:**
- Sortable table with all candle data
- Columns: Timestamp, Open, High, Low, Close, Volume, Ticks, Status
- Color-coded for complete vs. in-progress candles
- Easy to read datetime format

**Filtering:**
- Show last N candles (default: 50)
- Can be adjusted via query parameters

### 4. Configuration Management

**URL**: `http://localhost:5000/config`

**Update Settings:**
- **Candle Interval**: Choose 1, 5, 15, 30, or 60 minutes
- **Max Candles Stored**: Set limit per ticker (10-1000)
- **Max Tickers**: Maximum tracked tickers (1-100)
- **WebSocket Reconnect Delay**: Delay before reconnection (1-60s)
- **WebSocket Ping Interval**: Keep-alive interval (10-120s)

**Actions:**
- **Save Configuration**: Apply changes immediately
- **Reset to Defaults**: Restore .env file defaults

**Configuration Source:**
- Shows where current config is loaded from
- Displays active overrides

---

## Design

The admin UI follows the **sqowe brand guidelines** from `tmp/AI_WEB_DESIGN.md`:

### Colors

- **Primary**: Dark Ground (#222222), Light Purple (#8E88A3)
- **Secondary**: Light Grey (#B2B3B2), Dark Purple (#5B5377)
- **Gradient**: Linear gradient from Dark Purple to Light Purple

### Typography

- **Font Family**: Montserrat (Google Fonts)
- **Weights**: Light (300), Regular (400), Medium (500), Bold (700)
- **Responsive**: Fluid typography scales with screen size

### Components

- Professional card-based layout
- Responsive grid system
- Custom buttons with hover effects
- Clean table design
- Alert messages with auto-dismiss
- Status badges for visual feedback

---

## Architecture

### Technology Stack

**Backend:**
- Flask 3.0+ (Python web framework)
- Requests library for API communication
- Session-based authentication

**Frontend:**
- Jinja2 templates
- Vanilla JavaScript (no frameworks)
- Chart.js 4.4+ for visualizations
- Google Fonts (Montserrat)

**Process Management:**
- Supervisord manages both main API and admin UI
- Both services run in single Docker container
- Automatic restart on failure

### File Structure

```
src/admin/
├── __init__.py           # Package initialization
├── app.py                # Flask application
├── auth.py               # Authentication logic
├── api_client.py         # REST API client
├── templates/            # Jinja2 templates
│   ├── base.html         # Base template with navigation
│   ├── login.html        # Login page
│   ├── dashboard.html    # Dashboard page
│   ├── tickers.html      # Ticker management
│   ├── candles.html      # Candle viewer
│   └── config.html       # Configuration page
└── static/               # Static assets
    ├── css/
    │   └── admin.css     # sqowe brand styles
    ├── js/
    │   └── admin.js      # Interactive features
    └── img/
        ├── logo-dark.png # Dark logo variant
        └── logo-light.png # Light logo variant
```

### Communication Flow

```
User Browser
    ↓
Flask Admin UI (Port 5000)
    ↓ HTTP Requests
Main REST API (Port 8765)
    ↓
WebSocket Manager + Storage
    ↓
EODHD WebSocket + SQLite
```

---

## Troubleshooting

### Cannot Access Admin UI

**Symptom**: Browser shows "Connection refused"

**Solutions:**
1. Check if services are running: `docker-compose ps`
2. Check logs: `docker-compose logs admin_ui`
3. Verify port mapping: `docker-compose port eodhd-candles 5000`
4. Ensure `ADMIN_ENABLED=true` in `.env`

### Login Failed

**Symptom**: "Invalid API key" error

**Solutions:**
1. Verify `API_KEY` in `.env` matches what you're entering
2. Check for extra spaces or hidden characters
3. Restart container after changing `.env`: `docker-compose restart`

### Chart Not Displaying

**Symptom**: Blank chart area on candles page

**Solutions:**
1. Check browser console for JavaScript errors
2. Ensure Chart.js CDN is accessible
3. Verify candle data exists for selected ticker
4. Try different browser or clear cache

### Slow Performance

**Symptom**: Pages load slowly or timeout

**Solutions:**
1. Check main API health: `curl http://localhost:8765/health`
2. Review database statistics (may need cleanup)
3. Check Docker resource limits
4. Monitor logs for slow queries

### Cannot Access from Remote Machine

**Symptom**: Cannot reach admin UI from another computer

**Solutions:**
1. Default is `ADMIN_HOST=127.0.0.1` (localhost only)
2. For external access, set `ADMIN_HOST=0.0.0.0` (⚠️ see Security section)
3. Use SSH tunnel instead: `ssh -L 5000:localhost:5000 user@host`
4. Check firewall rules on Docker host

---

## Development

### Running Admin UI Standalone

For development purposes, you can run the admin UI separately:

```bash
# Set environment variables
export API_KEY=your_api_key
export ADMIN_HOST=127.0.0.1
export ADMIN_PORT=5000

# Run admin UI
python -m src.admin.app
```

### Testing

```bash
# Test API client connection
python -c "
from src.admin.api_client import APIClient
client = APIClient('http://localhost:8765', 'your_api_key')
print(client.get_health())
"

# Test authentication
python -c "
from src.admin.auth import verify_api_key
print(verify_api_key('test_key', 'test_key'))
"
```

---

## API Endpoints

The admin UI uses these API endpoints (all require authentication):

| Method | Endpoint | Used For |
|--------|----------|----------|
| GET | `/health` | Health check |
| GET | `/status` | Dashboard statistics |
| GET | `/config` | Load configuration |
| PATCH | `/config` | Update configuration |
| POST | `/config/reset` | Reset configuration |
| GET | `/tickers` | List tickers |
| POST | `/tickers` | Add tickers |
| DELETE | `/tickers/{ticker}` | Remove ticker |
| GET | `/candles/{ticker}` | Get candle data |
| POST | `/reconnect` | Reconnect WebSocket |

---

## Changelog

### v0.4.0 (2025-12-13)
- Initial release of admin web UI
- Dashboard with real-time monitoring
- Ticker management interface
- Candle data viewer with Chart.js
- Configuration management
- sqowe brand design implementation
- Supervisord integration for multi-process container

---

## Support

For issues or questions:

1. Check this documentation
2. Review main [README.md](../README.md)
3. Check Docker logs: `docker-compose logs`
4. Verify configuration in `.env`

---

## License

Part of the EODHD Real-Time Candle Aggregator project.

**Version**: 0.4.0
**Last Updated**: 2025-12-13
