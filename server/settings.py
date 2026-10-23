"""
Server settings, all read from environment variables.

On Railway you set these under your service's "Variables" tab. Nothing
sensitive is ever written into the repo.

Required:
  AGENT_TOKEN     shared secret every camera must present to connect
  VIEW_PASSWORD   password you type once in your phone's browser

Recommended:
  NTFY_TOPIC      your private ntfy.sh topic for push notifications
  PUBLIC_URL      https URL of this server (used in notification links)
"""

import os
import secrets


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _int(name, default):
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _float(name, default):
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


def _bool(name, default=False):
    v = _env(name).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


# -- auth --------------------------------------------------------------------
# Cameras authenticate with AGENT_TOKEN; you authenticate with VIEW_PASSWORD.
AGENT_TOKEN = _env("AGENT_TOKEN")
VIEW_PASSWORD = _env("VIEW_PASSWORD")

# Used to sign the login cookie. If unset we generate one per boot, which
# just means you log in again after a redeploy.
SECRET_KEY = _env("SECRET_KEY") or secrets.token_hex(32)
SESSION_DAYS = _int("SESSION_DAYS", 30)

# -- notifications -----------------------------------------------------------
NTFY_SERVER = _env("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = _env("NTFY_TOPIC")
NTFY_TOKEN = _env("NTFY_TOKEN")          # only for self-hosted / reserved topics

# Don't spam: minimum gap between pushes for the same camera + event kind.
NOTIFY_MIN_GAP = _float("NOTIFY_MIN_GAP", 90.0)

# Quiet hours (24h clock, server local time). Inside these hours only
# high-priority events (impact / person) push. "" disables quiet hours.
QUIET_FROM = _int("QUIET_FROM", -1)
QUIET_TO = _int("QUIET_TO", -1)

# -- live view ---------------------------------------------------------------
# Deliberately conservative so Railway egress stays cheap. The dashboard can
# ask for HD per camera when you actually need detail.
LIVE_WIDTH = _int("LIVE_WIDTH", 480)
LIVE_FPS = _float("LIVE_FPS", 4.0)
LIVE_QUALITY = _int("LIVE_QUALITY", 55)

HD_WIDTH = _int("HD_WIDTH", 1280)
HD_FPS = _float("HD_FPS", 12.0)
HD_QUALITY = _int("HD_QUALITY", 75)

# Snapshot mode: one still every N seconds. Cheapest way to peek.
SNAPSHOT_EVERY = _float("SNAPSHOT_EVERY", 2.0)

# -- evidence ----------------------------------------------------------------
# A copy of the footage an alert was raised during, kept here rather than
# only on the device - because for a theft, the device is what goes missing.
# Small on purpose: this is evidence, not the archive.
EVIDENCE = _bool("SAVE_EVENT_CLIPS", True)
EVIDENCE_DIR = _env("EVIDENCE_DIR", "evidence")
EVIDENCE_MAX_MB = _int("EVIDENCE_MAX_MB", 800)
EVIDENCE_DAYS = _int("EVIDENCE_DAYS", 14)
# Which alerts are worth a copy. Ordinary movement is not on this list: on a
# busy camera it would fill the store with nothing in minutes.
EVIDENCE_KINDS = tuple(
    k.strip() for k in
    _env("EVIDENCE_KINDS", "person,impact,moved,cry").split(",") if k.strip()
)

# -- housekeeping ------------------------------------------------------------
EVENT_HISTORY = _int("EVENT_HISTORY", 300)      # events kept in memory
# Where the history is written so it survives a restart. The pictures are
# not kept - they are the bulky part, and knowing that something happened
# outlives being able to see a thumbnail of it. On a host with an ephemeral
# disk, Railway among them, a redeploy still clears this.
EVENT_LOG = _env("EVENT_LOG", "evidence/events.json")
AGENT_TIMEOUT = _float("AGENT_TIMEOUT", 45.0)   # mark offline after silence
PUBLIC_URL = _env("PUBLIC_URL").rstrip("/")

# Railway injects this; falls back for local runs.
PORT = _int("PORT", 8080)


def public_url():
    """Best guess at this server's public base URL."""
    if PUBLIC_URL:
        return PUBLIC_URL
    # Railway exposes the generated domain here.
    for key in ("RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL"):
        v = _env(key)
        if v:
            return v if v.startswith("http") else f"https://{v}"
    return f"http://localhost:{PORT}"


def missing_required():
    """Names of settings that must be set before the server is usable."""
    missing = []
    if not AGENT_TOKEN:
        missing.append("AGENT_TOKEN")
    if not VIEW_PASSWORD:
        missing.append("VIEW_PASSWORD")
    return missing
