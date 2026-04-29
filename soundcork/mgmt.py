"""Management API endpoints for the ueberboese-app.

These endpoints are NOT part of the Bose SoundTouch protocol. They are
custom endpoints used by the ueberboese Flutter app for speaker management,
device events, and Spotify integration.

All endpoints require HTTP Basic Auth.
"""

import asyncio
import html
import logging
from typing import Annotated

import httpx

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from soundcork.config import Settings
from soundcork.datastore import DataStore
from soundcork.mgmt_auth import verify_credentials
from soundcork.spotify_service import SpotifyService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mgmt", tags=["management"])

datastore = DataStore()
settings = Settings()
spotify = SpotifyService()


# --- Speaker Management ---


@router.get("/accounts/{account_id}/speakers")
def list_speakers(
    account_id: str,
    _user: str = Depends(verify_credentials),
):
    """List all speakers for an account.

    Returns IP addresses and basic info for each device, so the app
    can connect to them directly on port 8090.
    """
    try:
        device_ids = datastore.list_devices(account_id)
    except (StopIteration, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Account not found")

    speakers = []
    for device_id in device_ids:
        try:
            info = datastore.get_device_info(account_id, device_id)
            speakers.append(
                {
                    "ipAddress": info.ip_address,
                    "name": info.name,
                    "deviceId": info.device_id,
                    "type": info.product_code,
                }
            )
        except Exception:
            logger.warning("Failed to read device info for %s", device_id)
            continue

    return {"speakers": speakers}


# --- Device Events ---


@router.get("/devices/{device_id}/events")
def list_device_events(
    device_id: str,
    limit: int = 100,
    _user: str = Depends(verify_credentials),
):
    """List persisted events for a device, newest first.

    Events are captured from speaker telemetry (scmudc/stapp) and WebSocket
    updates (nowPlaying, volume, preset changes). Returns at most `limit`
    events (default 100, max 200).
    """
    limit = min(max(1, limit), 200)
    events = datastore.get_events(device_id)
    return {"events": events[:limit], "total": len(events)}


# --- Spotify ---


@router.post("/spotify/init")
def spotify_init(
    request: Request,
    _user: str = Depends(verify_credentials),
):
    """Start the Spotify OAuth flow.

    Returns a redirect URL that the app should open in a browser.
    The user authorizes there, and Spotify redirects back to the
    configured redirect_uri (mobile deep link) with an authorization code.
    """
    if not settings.spotify_client_id:
        raise HTTPException(
            status_code=503,
            detail="Spotify integration not configured (missing SPOTIFY_CLIENT_ID)",
        )

    authorize_url = spotify.build_authorize_url()
    return {"redirectUrl": authorize_url}


@router.get("/spotify/init")
def spotify_init_browser(request: Request):
    """Start the Spotify OAuth flow via browser redirect.

    Unlike POST /spotify/init (used by the mobile app), this endpoint
    redirects the browser directly to Spotify with the server-side
    callback URL, so the entire flow happens in the browser.
    No Basic Auth required — the callback is on this server.
    """
    if not settings.spotify_client_id:
        raise HTTPException(
            status_code=503,
            detail="Spotify integration not configured (missing SPOTIFY_CLIENT_ID)",
        )

    # Use the server callback URL instead of the mobile deep link.
    # We use settings.base_url rather than request.base_url because the
    # app sits behind a TLS-terminating reverse proxy (Traefik) and
    # request.base_url returns http:// while Spotify requires the
    # registered https:// redirect URI.
    callback_url = settings.base_url.rstrip("/") + "/mgmt/spotify/callback"
    authorize_url = spotify.build_authorize_url(redirect_uri=callback_url)

    return RedirectResponse(url=authorize_url)


@router.get("/spotify/callback", response_class=HTMLResponse)
async def spotify_callback(
    request: Request,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
):
    """Server-side OAuth callback for web/localhost flows.

    This endpoint is NOT protected by Basic Auth because Spotify
    redirects the user's browser here directly.

    After exchanging the code for tokens, it shows a success page
    that the user can close.
    """
    if error:
        safe_error = html.escape(error)
        return HTMLResponse(
            content=f"<html><body><h1>Spotify Authorization Failed</h1><p>Error: {safe_error}</p></body></html>",
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            content="<html><body><h1>Missing authorization code</h1></body></html>",
            status_code=400,
        )

    try:
        # The redirect_uri must match what was used in the authorize request
        callback_url = settings.base_url.rstrip("/") + "/mgmt/spotify/callback"
        account = await spotify.exchange_code_and_store(code, redirect_uri=callback_url)
        safe_display_name = html.escape(str(account["displayName"]))
        safe_spotify_user_id = html.escape(str(account["spotifyUserId"]))
        return HTMLResponse(
            content=f"<html><body>"
            f"<h1>Spotify Connected</h1>"
            f"<p>Linked account: {safe_display_name} ({safe_spotify_user_id})</p>"
            f"<p>You can close this window.</p>"
            f"</body></html>"
        )
    except Exception:
        logger.exception("Spotify callback failed")
        return HTMLResponse(
            content="<html><body><h1>Error</h1><p>An internal error occurred. Please try again.</p></body></html>",
            status_code=500,
        )


@router.post("/spotify/confirm")
async def spotify_confirm(
    code: Annotated[str, Query()],
    _user: str = Depends(verify_credentials),
):
    """Confirm Spotify authorization with an authorization code.

    Used by the mobile app after the deep link callback delivers
    the code. Exchanges the code for tokens and stores the account.
    """
    if not settings.spotify_client_id:
        raise HTTPException(
            status_code=503,
            detail="Spotify integration not configured",
        )

    try:
        await spotify.exchange_code_and_store(code)
    except Exception:
        logger.exception("Spotify confirm failed")
        raise HTTPException(status_code=500, detail="An internal error has occurred")

    return {"ok": True}


@router.get("/spotify/accounts")
def spotify_accounts(
    _user: str = Depends(verify_credentials),
):
    """List connected Spotify accounts."""
    accounts = spotify.list_accounts()
    # Strip tokens from the response — the app only needs display info
    return {
        "accounts": [
            {
                "displayName": a["displayName"],
                "createdAt": a["createdAt"],
                "spotifyUserId": a["spotifyUserId"],
            }
            for a in accounts
        ]
    }


@router.get("/spotify/token")
def spotify_token(
    _user: str = Depends(verify_credentials),
):
    """Get a fresh Spotify access token and username.

    Used by the on-speaker boot primer to prime the ZeroConf endpoint
    without needing Spotify credentials stored on the device.
    """
    user_id = spotify.get_spotify_user_id()
    if not user_id:
        raise HTTPException(status_code=404, detail="No Spotify account linked")

    access_token = spotify.get_fresh_token_sync()
    if not access_token:
        raise HTTPException(status_code=503, detail="Failed to get Spotify token")

    return {"accessToken": access_token, "username": user_id}


@router.post("/spotify/activate-speaker")
async def activate_speaker(
    device_name: str = Query("Bose", description="Substring to match in Spotify Connect device name"),
    _user: str = Depends(verify_credentials),
):
    """Transfer the Spotify playback session to the speaker.

    After ZeroConf priming the speaker is registered with Spotify but
    idle.  This endpoint does what the Spotify desktop app does when you
    select the speaker: it calls ``PUT /v1/me/player`` to transfer the
    session, which triggers Spotify's servers to activate streaming on
    the device.  Called by the on-speaker boot primer after addUser.
    """
    try:
        result = await spotify.activate_speaker(device_name_hint=device_name)
        return result
    except Exception as e:
        logger.exception("Failed to activate speaker")
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/spotify/entity")
async def spotify_entity(
    request: Request,
    _user: str = Depends(verify_credentials),
):
    """Resolve a Spotify URI to a name and image URL.

    Used by the app when storing Spotify presets — it needs the
    track/album/playlist name and cover art to display in the UI.
    """
    body = await request.json()
    uri = body.get("uri", "")

    if not uri or not uri.startswith("spotify:"):
        raise HTTPException(status_code=400, detail={"message": "Invalid Spotify URI"})

    try:
        entity = await spotify.resolve_entity(uri)
        return entity
    except Exception as e:
        logger.exception("Failed to resolve Spotify entity: %s", uri)
        raise HTTPException(status_code=500, detail=str(e))

SPEAKER_PORT = 8090
SPEAKER_TIMEOUT = 10.0


def _content_item_xml_from_preset(preset) -> str:
    """Serialise a Preset model object to a ContentItem XML string."""
    return (
        f'<ContentItem source="{preset.source}" type="{preset.type}"'
        f' location="{preset.location}"'
        f' sourceAccount="{preset.source_account or ""}"'
        f' isPresetable="true">'
        f'<itemName>{preset.name}</itemName>'
        f'<containerArt>{preset.container_art or ""}</containerArt>'
        f'</ContentItem>'
    )


@router.post("/accounts/{account_id}/sync-presets")
async def sync_presets_to_all_speakers(
    account_id: str,
    source_device_id: str = Query(..., description="Device ID whose presets are the source of truth"),
    _user: str = Depends(verify_credentials),
):
    """Push Soundcork's Presets.xml to target speakers via SSH.

    Workflow per target device:
      1. Read Presets.xml from Soundcork's datastore (the source of truth).
      2. SSH into the speaker as root (no private key — host-key-only auth).
      3. Remount /mnt/nv read-write.
      4. Back up the existing Presets.xml.
      5. Write the new content atomically via a tmp file + mv.
      6. Remount /mnt/nv read-only.
      7. Send a PRESET_1 key press/release via port 8090 so the speaker
         reloads its in-memory preset cache immediately without a reboot.

    SSH options match what the speakers accept: ssh-rsa host key algorithm,
    no private key required (root login is open on these devices).
    """
    import os

    try:
        device_ids = datastore.list_devices(account_id)
    except (StopIteration, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Account not found")

    if source_device_id not in device_ids:
        raise HTTPException(status_code=404, detail=f"Source device {source_device_id!r} not found in account")

    # Read the raw Presets.xml bytes from the Soundcork datastore
    presets_file = os.path.join(datastore.account_dir(account_id), "Presets.xml")
    try:
        with open(presets_file, "rb") as f:
            presets_xml_bytes = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Presets.xml not found in datastore")

    try:
        presets = datastore.get_presets(account_id, source_device_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read presets: {exc}")

    if not presets:
        raise HTTPException(status_code=400, detail="Source device has no presets to sync")

    target_device_ids = [d for d in device_ids if d != source_device_id]
    if not target_device_ids:
        raise HTTPException(status_code=400, detail="No other devices in account to sync to")

    preset_summary = {str(p.id): p.name for p in presets}

    SPEAKER_PRESET_PATH = "/mnt/nv/BoseApp-Persistence/1/Presets.xml"
    SPEAKER_PRESET_BACKUP = "/mnt/nv/BoseApp-Persistence/1/Presets.xml.bak"
    SPEAKER_PRESET_TMP = "/mnt/nv/BoseApp-Persistence/1/Presets.xml.tmp"

    SSH_OPTS = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "HostKeyAlgorithms=+ssh-rsa",
        "-o", "PubkeyAuthentication=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
    ]

    # Single SSH session: remount rw, backup, write tmp, mv, remount ro
    # stdin receives the raw XML bytes; cat pipes them into the tmp file
    SSH_CMD = (
        f"mount -o remount,rw /mnt/nv && "
        f"cp {SPEAKER_PRESET_PATH} {SPEAKER_PRESET_BACKUP} && "
        f"cat > {SPEAKER_PRESET_TMP} && "
        f"mv {SPEAKER_PRESET_TMP} {SPEAKER_PRESET_PATH} && "
        f"mount -o remount,ro /mnt/nv"
    )

    async def ssh_write(ip: str) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", *SSH_OPTS, f"root@{ip}", SSH_CMD,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=presets_xml_bytes), timeout=20
            )
        except asyncio.TimeoutError:
            return {"status": "error", "detail": "SSH timed out"}
        except FileNotFoundError:
            return {"status": "error", "detail": "ssh binary not found on server"}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            return {"status": "error", "detail": f"SSH failed (exit {proc.returncode}): {err}"}

        return {"status": "ok"}

    async def key_cycle(ip: str) -> None:
        """Send PRESET_1 press+release to trigger in-memory preset reload."""
        key_url = f"http://{ip}:{SPEAKER_PORT}/key"
        headers = {"Content-Type": "text/xml"}
        try:
            async with httpx.AsyncClient(timeout=SPEAKER_TIMEOUT) as client:
                for state in ("press", "release"):
                    await client.post(
                        key_url,
                        content=f'<key state="{state}" sender="Gabbo">PRESET_1</key>'.encode(),
                        headers=headers,
                    )
        except Exception:
            pass  # Non-fatal: file is written; speaker will reload on next reboot at worst

    results: list[dict] = []
    for device_id in target_device_ids:
        try:
            info = datastore.get_device_info(account_id, device_id)
        except Exception:
            results.append({
                "deviceId": device_id,
                "status": "error",
                "detail": "Could not read device info",
                "presets": [],
            })
            continue

        ip = info.ip_address
        write_result = await ssh_write(ip)

        if write_result["status"] == "ok":
            await key_cycle(ip)

        preset_list = [{"slot": str(p.id), "name": p.name, "status": write_result["status"]} for p in presets]
        results.append({
            "deviceId": device_id,
            "name": info.name,
            "ip": ip,
            "status": write_result["status"],
            "presets": preset_list,
            "summary": preset_summary,
            "detail": (
                f"All {len(presets)} preset(s) written to speaker filesystem and cache refreshed"
                if write_result["status"] == "ok"
                else write_result["detail"]
            ),
        })

    return {"synced": results, "source": source_device_id}
