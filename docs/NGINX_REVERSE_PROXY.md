# Nginx Reverse Proxy Configuration

This document provides nginx reverse proxy configuration for the EODHD Real-Time Candle Aggregator Admin UI.

---

## Solution 1: URL Prefix with Rewrite (Recommended)

This approach uses nginx's rewrite rules to strip the prefix before forwarding to Flask.

### Nginx Configuration

```nginx
location /eodhd/admin/ {
    # Rewrite the URL to remove the prefix
    rewrite ^/eodhd/admin/(.*) /$1 break;

    proxy_pass http://172.28.0.200:5000;
    proxy_http_version 1.1;

    # Forward original host and protocol
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /eodhd/admin;

    # WebSocket support (if needed later)
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Buffering
    proxy_buffering off;
    proxy_request_buffering off;

    # Timeouts
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    proxy_connect_timeout 600s;

    # File upload size
    client_max_body_size 16M;
}

# Redirect root to include trailing slash
location = /eodhd/admin {
    return 301 /eodhd/admin/;
}
```

### Flask Configuration

No changes needed in Flask - the rewrite strips the prefix before it reaches the app.

**In your `.env` file:**
```bash
ADMIN_HOST=0.0.0.0
ADMIN_PORT=5000
# No ADMIN_URL_PREFIX needed for this solution
```

---

## Solution 2: URL Prefix in Flask (Alternative)

This approach configures Flask to handle the URL prefix natively.

### Nginx Configuration

```nginx
location /eodhd/admin {
    proxy_pass http://172.28.0.200:5000;
    proxy_http_version 1.1;

    # Important: Forward the prefix
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /eodhd/admin;

    # WebSocket support
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Buffering
    proxy_buffering off;
    proxy_request_buffering off;

    # Timeouts
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    proxy_connect_timeout 600s;

    # File upload size
    client_max_body_size 16M;
}
```

### Flask Configuration

**In your `.env` file:**
```bash
ADMIN_HOST=0.0.0.0
ADMIN_PORT=5000
ADMIN_URL_PREFIX=/eodhd/admin
```

**Or in docker-compose.yml:**
```yaml
environment:
  - ADMIN_URL_PREFIX=/eodhd/admin
```

---

## Complete Nginx Example

Here's a complete example with both the main API and admin UI:

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # Main REST API
    location /eodhd/api/ {
        rewrite ^/eodhd/api/(.*) /$1 break;

        proxy_pass http://172.28.0.200:8765;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_connect_timeout 600s;

        client_max_body_size 16M;
    }

    # Admin Web UI (with rewrite)
    location /eodhd/admin/ {
        rewrite ^/eodhd/admin/(.*) /$1 break;

        proxy_pass http://172.28.0.200:5000;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Prefix /eodhd/admin;

        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_buffering off;
        proxy_request_buffering off;

        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_connect_timeout 600s;

        client_max_body_size 16M;
    }

    # Redirect without trailing slash
    location = /eodhd/admin {
        return 301 /eodhd/admin/;
    }

    location = /eodhd/api {
        return 301 /eodhd/api/;
    }
}
```

---

## Troubleshooting

### 1. **404 Not Found on CSS/JS files**

**Problem**: Static files (CSS, JS) return 404 errors.

**Solution**: Use Solution 1 (rewrite method) or ensure Flask is properly configured for URL prefix.

Check browser console for the exact URLs being requested.

### 2. **Redirect Loops**

**Problem**: Page keeps redirecting infinitely.

**Solution**: Ensure you're forwarding the `X-Forwarded-Proto` header correctly:

```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

### 3. **Session Not Persisting**

**Problem**: Login doesn't work or session expires immediately.

**Solution**: Ensure cookies are being set correctly. Check that:
- HTTPS is properly configured
- `X-Forwarded-Proto` header is set
- Flask's session secret is configured

### 4. **Static Files Have Wrong Path**

**Problem**: Static files load but have incorrect URLs.

**Solution**: Use the rewrite method (Solution 1) which is simpler and more reliable.

---

## Testing the Configuration

### 1. Test nginx configuration syntax

```bash
nginx -t
```

### 2. Reload nginx

```bash
nginx -s reload
# or
systemctl reload nginx
```

### 3. Test the endpoints

```bash
# Test admin UI
curl -I https://your-domain.com/eodhd/admin/

# Test main API
curl -H "X-API-Key: your_key" https://your-domain.com/eodhd/api/health
```

### 4. Check nginx logs

```bash
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

### 5. Check Flask logs

```bash
docker-compose logs -f admin_ui
```

---

## Security Considerations

### 1. **IP Restrictions**

Limit admin access to specific IPs:

```nginx
location /eodhd/admin/ {
    allow 192.168.1.0/24;
    allow 10.0.0.0/8;
    deny all;

    # ... rest of proxy configuration
}
```

### 2. **Basic Authentication**

Add an extra layer of authentication:

```bash
# Create password file
htpasswd -c /etc/nginx/.htpasswd admin_user
```

```nginx
location /eodhd/admin/ {
    auth_basic "Admin Area";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # ... rest of proxy configuration
}
```

### 3. **Rate Limiting**

Prevent brute force attacks:

```nginx
# Define rate limit zone (in http block)
limit_req_zone $binary_remote_addr zone=admin_limit:10m rate=10r/m;

# Apply to location
location /eodhd/admin/ {
    limit_req zone=admin_limit burst=5 nodelay;

    # ... rest of proxy configuration
}
```

### 4. **HTTPS Only**

Redirect HTTP to HTTPS:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}
```

---

## Recommended Configuration

**For production use, I recommend:**

1. ✅ **Solution 1** (rewrite method) - Simpler, more reliable
2. ✅ **HTTPS** with valid SSL certificate
3. ✅ **IP restrictions** for admin UI
4. ✅ **Rate limiting** on login endpoints
5. ✅ **Basic auth** as additional security layer (optional)

**Configuration Summary:**

**.env:**
```bash
ADMIN_HOST=0.0.0.0
ADMIN_PORT=5000
# No URL prefix needed
```

**nginx:**
```nginx
location /eodhd/admin/ {
    # IP restrictions
    allow 192.168.1.0/24;
    deny all;

    # Rewrite to strip prefix
    rewrite ^/eodhd/admin/(.*) /$1 break;

    proxy_pass http://172.28.0.200:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_buffering off;
    proxy_read_timeout 600s;
}
```

---

## Version

- **Document Version**: 1.0
- **Last Updated**: 2025-12-13
- **Application Version**: 0.4.0
