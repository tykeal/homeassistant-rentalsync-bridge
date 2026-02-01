<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Deployment Guide

This guide covers production deployment considerations for RentalSync Bridge.

## Deployment Options

### Option 1: Home Assistant Add-on (Recommended)

The simplest deployment method for Home Assistant users. See
[homeassistant-addon-setup.md](homeassistant-addon-setup.md).

### Option 2: Standalone Container

Run as a standalone container for non-Home Assistant deployments.

```bash
docker run -d \
  --name rentalsync-bridge \
  -p 8099:8099 \
  -v ./data:/data \
  -e STANDALONE_MODE=true \
  -e DATABASE_URL=sqlite:///data/rentalsync.db \
  -e CLOUDBEDS_API_KEY=your-api-key \
  ghcr.io/tykeal/rentalsync-bridge:latest
```

### Option 3: Podman Deployment

```bash
podman run -d \
  --name rentalsync-bridge \
  -p 8099:8099 \
  -v ./data:/data:Z \
  -e STANDALONE_MODE=true \
  -e DATABASE_URL=sqlite:///data/rentalsync.db \
  ghcr.io/tykeal/rentalsync-bridge:latest
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `STANDALONE_MODE` | Enable standalone mode (bypass HA auth) | `false` |
| `DATABASE_URL` | SQLite database path | `sqlite:///data/rentalsync.db` |
| `CLOUDBEDS_API_KEY` | Cloudbeds API key | - |
| `CLOUDBEDS_CLIENT_ID` | OAuth client ID | - |
| `CLOUDBEDS_CLIENT_SECRET` | OAuth client secret | - |
| `LOG_LEVEL` | Logging level | `INFO` |
| `PORT` | HTTP port | `8099` |

## HTTPS Configuration

**Important:** Production deployments MUST use HTTPS for security.

### Option A: Reverse Proxy (Recommended)

Place RentalSync Bridge behind a TLS-terminating reverse proxy.

**nginx example:**

```nginx
server {
    listen 443 ssl http2;
    server_name rentalsync.example.com;

    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8099;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Traefik example (docker-compose):**

```yaml
services:
  rentalsync:
    image: ghcr.io/tykeal/rentalsync-bridge:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.rentalsync.rule=Host(`rentalsync.example.com`)"
      - "traefik.http.routers.rentalsync.tls.certresolver=letsencrypt"
```

### Option B: Home Assistant Ingress

Home Assistant handles HTTPS automatically when using Ingress mode. No
additional configuration is required.

### HTTP-Only Deployments

HTTP without TLS is acceptable **only** when:
- Running behind a TLS-terminating proxy
- In development/testing environments
- On isolated private networks

Never expose HTTP directly to the internet.

## Database Backup

### SQLite Backup

The database is stored at the path specified by `DATABASE_URL` (default:
`/data/rentalsync.db`).

**Manual backup:**

```bash
# Stop the service first for consistent backup
docker stop rentalsync-bridge

# Copy the database files
cp /path/to/data/rentalsync.db /backup/rentalsync-$(date +%Y%m%d).db
cp /path/to/data/rentalsync.db-wal /backup/ 2>/dev/null || true
cp /path/to/data/rentalsync.db-shm /backup/ 2>/dev/null || true

# Restart the service
docker start rentalsync-bridge
```

**Automated backup script:**

```bash
#!/bin/bash
# backup-rentalsync.sh
BACKUP_DIR=/backup
DATA_DIR=/data
DATE=$(date +%Y%m%d_%H%M%S)

# SQLite online backup (no downtime)
sqlite3 $DATA_DIR/rentalsync.db ".backup $BACKUP_DIR/rentalsync-$DATE.db"

# Retain last 7 days
find $BACKUP_DIR -name "rentalsync-*.db" -mtime +7 -delete
```

### Restore from Backup

```bash
# Stop the service
docker stop rentalsync-bridge

# Replace database
cp /backup/rentalsync-20260131.db /data/rentalsync.db

# Start the service
docker start rentalsync-bridge
```

## Monitoring

### Health Check

```bash
curl http://localhost:8099/health
```

**Expected response:**
```json
{"status": "healthy", "version": "0.1.0"}
```

### Docker Health Check

The container includes a health check. Monitor with:

```bash
docker inspect --format='{{.State.Health.Status}}' rentalsync-bridge
```

### Logs

```bash
# Docker
docker logs -f rentalsync-bridge

# Podman
podman logs -f rentalsync-bridge

# Home Assistant Add-on
# View in the add-on Log tab
```

## Security Considerations

1. **API Keys**: Store Cloudbeds API keys securely (environment variables or
   secrets management)

2. **Network Isolation**: Run on a private network when possible

3. **Regular Updates**: Keep the container image updated

4. **Backup Encryption**: Encrypt backups containing booking data

5. **Access Control**: Limit access to the admin interface

## Troubleshooting

### Container Won't Start

1. Check logs: `docker logs rentalsync-bridge`
2. Verify volume permissions
3. Ensure port 8099 is available

### Database Locked Errors

SQLite WAL mode is enabled for better concurrency. If you see "database is
locked" errors:

1. Ensure only one container instance is running
2. Check disk space
3. Verify volume isn't mounted read-only

### Sync Failing

1. Check Cloudbeds API key is valid
2. Verify network connectivity to Cloudbeds API
3. Review error messages in logs
4. Check rate limiting isn't exceeded
