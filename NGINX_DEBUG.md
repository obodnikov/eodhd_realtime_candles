# Nginx Admin UI Debugging Guide

## Issue
`https://n8n.sqowe.com/eodhd/admin/` shows n8n interface instead of admin UI

## Debugging Steps

### 1. Check if Admin UI is Running

```bash
# Check if the container is running
docker ps | grep eodhd

# Check admin UI logs
docker-compose logs admin_ui

# Check if port 5000 is listening
docker exec eodhd-candle-aggregator netstat -tuln | grep 5000
# OR
docker exec eodhd-candle-aggregator ss -tuln | grep 5000

# Test admin UI directly from host
curl -I http://172.28.0.200:5000/
```

**Expected**: Should see Flask response, not connection refused

---

### 2. Check Nginx Configuration

```bash
# View nginx config
cat /etc/nginx/sites-enabled/n8n.sqowe.com
# OR wherever your config is

# Test nginx configuration syntax
nginx -t

# Check nginx error logs
tail -f /var/log/nginx/error.log

# Check nginx access logs
tail -f /var/log/nginx/access.log
```

---

### 3. Verify Location Block Order

**CRITICAL**: Nginx processes location blocks in a specific order. The `/eodhd/admin/` block MUST come BEFORE any catch-all blocks.

```nginx
server {
    listen 443 ssl;
    server_name n8n.sqowe.com;

    # ADMIN UI MUST COME FIRST (or before catch-all)
    location /eodhd/admin/ {
        rewrite ^/eodhd/admin/(.*) /$1 break;
        proxy_pass http://172.28.0.200:5000;
        # ... headers ...
    }

    location = /eodhd/admin {
        return 301 /eodhd/admin/;
    }

    # n8n or other services AFTER
    location / {
        proxy_pass http://n8n-backend;
        # ...
    }
}
```

---

### 4. Common Issues & Fixes

#### Issue A: Admin UI Service Not Running

**Symptoms**:
```bash
curl http://172.28.0.200:5000/
# Returns: Connection refused
```

**Fix**:
```bash
# Rebuild and restart
docker-compose down
docker-compose build
docker-compose up -d

# Check logs
docker-compose logs -f admin_ui
```

---

#### Issue B: Wrong IP Address

**Symptoms**: Admin service running but nginx can't reach it

**Check**:
```bash
# Find actual container IP
docker inspect eodhd-candle-aggregator | grep IPAddress

# Or use container name instead of IP
```

**Fix** - Use container name in nginx:
```nginx
location /eodhd/admin/ {
    rewrite ^/eodhd/admin/(.*) /$1 break;
    # Use container name instead of IP
    proxy_pass http://eodhd-candle-aggregator:5000;
    # ...
}
```

---

#### Issue C: Location Block Priority

**Symptoms**: Always shows n8n, never admin UI

**Problem**: Nginx is matching a different location block first

**Check your config** - print all location blocks:
```bash
grep -A 5 "location " /etc/nginx/sites-enabled/n8n.sqowe.com
```

**Fix**: Move admin location block ABOVE catch-all locations:
```nginx
# CORRECT ORDER:
# 1. Exact matches (location = /foo)
# 2. Prefix matches (location /foo/)
# 3. Catch-all (location /)

location = /eodhd/admin {
    return 301 /eodhd/admin/;
}

location /eodhd/admin/ {
    # admin config
}

location / {
    # n8n config (catch-all LAST)
}
```

---

#### Issue D: Docker Network Isolation

**Symptoms**: Admin running, but nginx in different network can't reach it

**Check**:
```bash
# Check nginx container network
docker inspect nginx-container | grep NetworkMode

# Check admin container network
docker inspect eodhd-candle-aggregator | grep NetworkMode
```

**Fix**: Put nginx and admin in same network or use host network

**Option 1** - Update docker-compose.yml:
```yaml
services:
  eodhd-candles:
    networks:
      - n8n_network  # Same network as nginx

networks:
  n8n_network:
    external: true
```

**Option 2** - Use host network:
```yaml
services:
  eodhd-candles:
    network_mode: host
```

Then nginx uses `http://localhost:5000` instead of IP

---

### 5. Test the Flow

**Test 1**: Direct connection to admin UI
```bash
# From nginx server, test connection
curl -I http://172.28.0.200:5000/

# Should return:
# HTTP/1.1 302 FOUND
# Location: /login
```

**Test 2**: Test nginx rewrite
```bash
# Add test endpoint
curl -I https://n8n.sqowe.com/eodhd/admin/ -v

# Check response headers - should show redirect to /login
```

**Test 3**: Check nginx error log while accessing
```bash
# In one terminal
tail -f /var/log/nginx/error.log

# In another, access the URL
curl https://n8n.sqowe.com/eodhd/admin/
```

---

## Working Configuration Template

```nginx
server {
    listen 443 ssl http2;
    server_name n8n.sqowe.com;

    # SSL certificates
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # EODHD Admin UI - MUST COME BEFORE catch-all
    location = /eodhd/admin {
        return 301 /eodhd/admin/;
    }

    location /eodhd/admin/ {
        # Strip prefix
        rewrite ^/eodhd/admin/(.*) /$1 break;

        # Proxy to admin UI
        proxy_pass http://172.28.0.200:5000;
        proxy_http_version 1.1;

        # Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Buffering
        proxy_buffering off;
        proxy_request_buffering off;

        # Timeouts
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_connect_timeout 600s;

        # File size
        client_max_body_size 16M;
    }

    # EODHD API
    location /eodhd/api/ {
        rewrite ^/eodhd/api/(.*) /$1 break;
        proxy_pass http://172.28.0.200:8765;
        # ... headers ...
    }

    # n8n - catch-all LAST
    location / {
        proxy_pass http://n8n-backend;
        # ... n8n config ...
    }
}
```

---

## Quick Diagnosis Commands

Run these in order:

```bash
# 1. Is admin container running?
docker ps | grep eodhd
# Expected: Shows running container

# 2. Is admin UI responding?
curl -I http://172.28.0.200:5000/
# Expected: HTTP/1.1 302 or 200

# 3. Is nginx config valid?
nginx -t
# Expected: syntax is ok, test is successful

# 4. What's in nginx error log?
tail -20 /var/log/nginx/error.log
# Expected: No errors related to /eodhd/admin

# 5. What's nginx seeing?
tail -f /var/log/nginx/access.log | grep eodhd
# Then access https://n8n.sqowe.com/eodhd/admin/
# Expected: Shows proxy_pass to 172.28.0.200:5000
```

---

## Most Likely Issues

Based on the symptom (showing n8n instead of admin):

1. **90% chance**: Location block order - admin block is AFTER the n8n catch-all
2. **5% chance**: Wrong IP/network - nginx can't reach admin service
3. **5% chance**: Admin service not running - check `docker-compose logs`

---

## Contact Info

If still not working after these steps, provide:

1. Output of: `docker ps | grep eodhd`
2. Output of: `curl -I http://172.28.0.200:5000/`
3. Your nginx config: `cat /etc/nginx/sites-enabled/n8n.sqowe.com`
4. Nginx error log: `tail -50 /var/log/nginx/error.log`
5. Admin logs: `docker-compose logs --tail=50 admin_ui`
