# SoundCork 🔊 
> **Goal:** Keep your Bose SoundTouch speaker fully functional after Bose shuts down their cloud servers on May 6, 2026.

SoundCork is a self-hosted replacement that emulates the four critical Bose cloud services locally. This means all control and streaming responses happen entirely within your network—no traffic to Bose, no risk of unwanted firmware updates, and complete data privacy.

Credits: [Thanks To](https://github.com/jelliuk/soundcork#-credits)

## 🔨 Deployment Status
[![Development Build](https://github.com/jelliuk/soundcork/actions/workflows/dev-deploy.yml/badge.svg)](https://github.com/jelliuk/soundcork/actions/workflows/dev-deploy.yml)
[![Main Build](https://github.com/jelliuk/soundcork/actions/workflows/ci.yml/badge.svg)](https://github.com/jelliuk/soundcork/actions/workflows/ci.yml)
[![Security](https://github.com/jelliuk/soundcork/actions/workflows/security.yml/badge.svg)](https://github.com/jelliuk/soundcork/actions/workflows/security.yml)

## 📌 Service status

[deborahgu/soundcork](https://github.com/deborahgu/soundcork) kindly maintains a forum post with the [Current Status of Bose Cloud Services](https://github.com/deborahgu/soundcork/discussions/181).

---
## 👍 What Works

| Feature | Status | Notes |
|---------|--------|-------|
| TuneIn radio presets | Working | Presets 1-6, station playback |
| Spotify Connect | Working | Cast from the Spotify app — independent of Bose servers |
| Spotify presets | Working | Requires a [one-time kick-start](docs/spotify.md#fixing-spotify-presets) via Spotify Connect |
| Web UI | Working | Browser-based speaker control at `/webui/` |
| SSO/OIDC authentication | Working | Optional — works with any OIDC provider (Authentik, Keycloak, Auth0, etc.) |
| AUX input | Working | Not affected by shutdown |
| Bluetooth | Working | Not affected by shutdown |
| Firmware updates | Blocked | SoundCork returns "no updates available" |
| SoundTouch app presets | Working | Configure TuneIn presets via the Web UI or [Bose CLI](https://github.com/timvw/bose) |

---
## 👓 Screenshots

| Login (SSO) | Dashboard |
|:-----------:|:---------:|
| ![Login](docs/screenshots/login-sso.png) | ![Dashboard](docs/screenshots/dashboard.png) |

| Speaker Controls & Presets | Manage Presets |
|:--------------------------:|:--------------:|
| ![Speaker](docs/screenshots/speaker-controls.png) | ![Presets](docs/screenshots/manage-presets.png) |

| Preset Detail | Set Spotify Preset |
|:-------------:|:------------------:|
| ![Preset Detail](docs/screenshots/preset-detail.png) | ![Spotify Preset](docs/screenshots/edit-spotify-preset.png) |

| Search TuneIn Stations | Station Detail & Save |
|:----------------------:|:---------------------:|
| ![TuneIn Search](docs/screenshots/edit-tunein-search.png) | ![TuneIn Detail](docs/screenshots/edit-tunein-detail.png) |

| Internet Radio Preset | Spotify Accounts |
|:---------------------:|:----------------:|
| ![Radio Preset](docs/screenshots/edit-radio-preset.png) | ![Spotify](docs/screenshots/spotify.png) |

---
## ⚙️ Prerequisites

Before deploying SoundCork, you must prepare your speaker and data. Follow these steps in order:

1. **Enable SSH Access**: Get SSH access to your speaker via [Speaker Setup Guide](docs/speaker-setup.md#step-1-enable-ssh-access)
2. **Extract Speaker Data:** Extract necessary configuration files (presets, sources) from the speaker using [Speaker Setup Guide](docs/speaker-setup.md#step-2-extract-speaker-data)
3. **Deploy SoundCork:** Deploy the service on your network using [Deployment Guide](docs/deployment.md)
4. **Redirect Speaker:** Point your Bose device to your new local server via [Speaker Setup Guide](docs/speaker-setup.md#step-3-redirect-speaker-to-soundcork)

---
## 🚀 Quick Start Guide (Docker)

If you have Docker installed, this is the fastest way to get running:
```bash
docker run -d --name soundcork \
  -p 8000:8000 \
  -v ./data:/soundcork/data \
  -e base_url=http://your-server:8000 \
  -e data_dir=/soundcork/data \
  ghcr.io/jelliuk/soundcork:latest
```

Verify it's running:
```bash
curl http://your-server:8000/
# {"Bose":"Can't Brick Us"}
```

Access the Web UI at `http://your-server:8000/webui/`.

The container image supports `linux/amd64`.

See [Deployment Guide](docs/deployment.md) for Docker Compose.

---
## 🔓 Authentication

The Web UI is protected by session-based authentication. By default it uses a username/password login (configured via `MGMT_USERNAME`/`MGMT_PASSWORD`).

Optionally, you can enable **SSO via any OpenID Connect provider** (Authentik, Keycloak, Auth0, Google, Okta, etc.):

```bash
docker run -d --name soundcork \
  -p 8000:8000 \
  -v ./data:/soundcork/data \
  -e base_url=http://your-server:8000 \
  -e data_dir=/soundcork/data \
  -e OIDC_ISSUER_URL=https://your-provider/application/o/soundcork/ \
  -e OIDC_CLIENT_ID=soundcork \
  -e OIDC_CLIENT_SECRET=your-secret \
  ghcr.io/jelliuk/soundcork:latest
```

When OIDC is configured, the login page shows a "Sign in with SSO" button. When it's not configured, the password form is shown. See [Deployment Guide](docs/deployment.md#authentication) for details.

---
## ❓ How It Works

SoundTouch speakers communicate with four Bose cloud servers. SoundCork replaces all of them by editing the speaker's configuration to point to your server instead.

See [Architecture](docs/architecture.md) for details on the Bose servers, operating modes, and data flows.

---
## 💻 Bose CLI

The [Bose CLI](https://github.com/timvw/bose) talks directly to the speaker's local API (port 8090) and works independently of any cloud server:

```bash
brew install timvw/tap/bose
bose preset    # view presets
bose status    # speaker status
bose volume 30 # set volume
```

---
## 📰 Documentation

- [Speaker Setup Guide](docs/speaker-setup.md) — SSH access, data extraction, speaker redirect
- [Deployment Guide](docs/deployment.md) — Docker, Docker Compose, Kubernetes, bare metal
- [Architecture](docs/architecture.md) — Bose servers, proxy modes, circuit breaker, data flows
- [Spotify Guide](docs/spotify.md) — Spotify Connect vs SoundTouch Spotify, preset fix
- [API Specification](docs/API_Spec.md) — Reverse-engineered Bose server API
- [Shutdown Emulation](docs/Shutdown_Emulation.md) — Test results without Bose servers

---
## ⭐ Credits

- [deborahgu](https://github.com/deborahgu) for creating the original [soundcork](https://github.com/deborahgu/soundcork) project
- [timvw/soundcork](https://github.com/timvw) for the initial Docker support, smart proxy mode, and deployment guides.
- Bose for publishing the [SoundTouch Web API documentation](https://assets.bosecreative.com/m/496577402d128874/original/SoundTouch-Web-API.pdf) to support community developers.

---
## ❗License

MIT — see [LICENSE](LICENSE)
