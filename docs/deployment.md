# Deployment Guide

Four ways to run SoundCork, from simplest to most customizable.

## Option 1: Docker (Simplest)

```bash
docker run -d --name soundcork \
  -p 8000:8000 \
  -v /path/to/your/data:/soundcork/data \
  -e base_url=http://your-server:8000 \
  -e data_dir=/soundcork/data \
  ghcr.io/jelliuk/soundcork:latest
```

## Option 2: Docker Compose

Create a `docker-compose.yml`:

```yaml
services:
  soundcork:
    image: ghcr.io/jelliuk/soundcork:latest
    ports:
      - "8000:8000"
    environment:
      - base_url=http://your-server:8000
      - data_dir=/soundcork/data
      - SOUNDCORK_MODE=local
      - SOUNDCORK_LOG_DIR=/soundcork/logs/traffic
      - MGMT_USERNAME=admin
      - MGMT_PASSWORD=change_me!
      # Optional: OIDC/SSO authentication (see Authentication section below)
      # - OIDC_ISSUER_URL=https://your-provider/application/o/soundcork/
      # - OIDC_CLIENT_ID=soundcork
      # - OIDC_CLIENT_SECRET=your-secret
    volumes:
      - ./data:/soundcork/data
      - ./logs:/soundcork/logs
    restart: unless-stopped
```

Then run:

```bash
docker compose up -d
```

## Environment Variables

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `base_url` | `""` | Public URL of your SoundCork instance (e.g., `https://soundcork.example.com`) |
| `data_dir` | `""` | Path to the speaker data directory |
| `SOUNDCORK_MODE` | `local` | `local` (recommended) or `proxy` — see [Architecture](architecture.md) |
| `SOUNDCORK_LOG_DIR` | `./logs/traffic` | Directory for traffic logs (proxy mode only) |

### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `MGMT_USERNAME` | `admin` | Username for password-based WebUI and management API login |
| `MGMT_PASSWORD` | `change_me!` | Password for password-based WebUI and management API login |
| `OIDC_ISSUER_URL` | `""` | OIDC provider issuer URL (e.g. `https://tinyauth.example.com`)|
| `OIDC_CLIENT_ID` | `""` | OIDC client ID |
| `OIDC_CLIENT_SECRET` | `""` | OIDC client secret |

When all three `OIDC_*` variables are set, the WebUI login page shows a "Sign in with SSO" button and authenticates users via your OIDC provider. When any is empty, the WebUI falls back to password-based login using `MGMT_USERNAME`/`MGMT_PASSWORD`.

### Spotify (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `SPOTIFY_CLIENT_ID` | `""` | Spotify OAuth client ID — see [Spotify Guide](spotify.md) |
| `SPOTIFY_CLIENT_SECRET` | `""` | Spotify OAuth client secret |

## Authentication

SoundCork has three layers of authentication:

| Layer | Protects | Mechanism |
|-------|----------|-----------|
| Speaker IP allowlist | `/marge/*`, `/bmx/*`, `/oauth/*` (Bose protocol endpoints) | Only registered speaker IPs can reach these endpoints |
| WebUI session auth | `/webui/*` | Cookie-based sessions — login via password or OIDC |
| Management API auth | `/mgmt/*` | HTTP Basic Auth (`MGMT_USERNAME`/`MGMT_PASSWORD`) |

### OIDC / SSO Setup

SoundCork supports authentication via any standard OpenID Connect provider. It uses the authorization code flow with PKCE and auto-discovers endpoints from the provider's `.well-known/openid-configuration`.

**Tested providers:** [TinyAuth.](https://github.com/steveiliop56/tinyauth) Any OIDC-compliant provider should work.

**Steps:**

1. Create an OAuth2/OIDC application in your provider with:
   - **Client type:** Confidential
   - **Grant type:** Authorization Code
   - **Redirect URI:** `https://your-soundcork-url/auth/callback`
   - **Scopes:** `openid`, `email`, `profile`

2. Set the three environment variables:
   ```bash
   OIDC_ISSUER_URL=https://tinyauth.example.com
   OIDC_CLIENT_ID=soundcork
   OIDC_CLIENT_SECRET=your-client-secret
   ```

3. Restart SoundCork. The login page will now show a "Sign in with SSO" button.

**Notes:**
- Password login remains available as a fallback even when OIDC is enabled (the password form is hidden from the UI but the API endpoint still works).
- Sessions are in-memory — on server restart, users simply re-authenticate (seamless if already logged in to the SSO provider).
- Logout clears the SoundCork session only; it does not log the user out of the SSO provider.
- For local development, add `http://localhost:8000/auth/callback` as an additional redirect URI in your provider.

## Container Image

- **Image:** `ghcr.io/jelliuk/soundcork:latest`
- **Multi-architecture:** `linux/amd64`
- Built automatically via GitHub Actions on every push to main
- Source: see `.github/workflows/ci.yml`

## Verifying It Works

```bash
curl http://your-server:8000/
# Expected: {"Bose":"Can't Brick Us"}
```

After redirecting your speaker (see [Speaker Setup](speaker-setup.md)), you should see incoming requests in the server logs.
