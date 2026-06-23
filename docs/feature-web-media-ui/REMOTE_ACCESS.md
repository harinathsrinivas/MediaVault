# MediaVault Web Console — Remote Access Guide

This document covers the two access modes for the MediaVault web console, the one-time setup steps, and the security model.

---

## Access Modes

### Mode 1 — Raw LAN or Tailscale IP (HTTP)

The app binds `0.0.0.0:<port>` (default **8765**), so any device that can reach the machine's IP address can open the console directly over plain HTTP:

| Client | URL |
|--------|-----|
| Same machine | `http://127.0.0.1:8765` |
| LAN device | `http://<lan-ip>:8765` |
| Tailscale device (raw) | `http://<tailscale-ip>:8765` |

**Note:** HTTP on a raw IP is plaintext on your LAN/tailnet. For sensitive use, prefer Mode 2.

All `/api/*` routes require the shared token (see [Token requirement](#token-requirement)) regardless of which access mode you use.

---

### Mode 2 — HTTPS via `tailscale serve` (Recommended)

`tailscale serve` reverse-proxies from a Tailscale-managed HTTPS URL to the local HTTP server. No port is opened to the public internet.

```
https://<machine>.<tailnet>.ts.net  →  http://127.0.0.1:8765
```

Tailscale handles TLS termination and tailnet routing; the app sees plain HTTP from `127.0.0.1`. This is **tailnet-only** — only devices logged into your tailnet can reach it.

> **Important:** We use `serve`, NOT `funnel`. `funnel` would expose the console to the public internet. MediaVault should never be publicly accessible.

---

## One-Time Admin Console Prerequisites

These are global Tailscale account settings. Do them once; they apply to all machines.

1. **Enable MagicDNS**
   Tailscale admin console → **DNS** → toggle **MagicDNS** on.

2. **Enable HTTPS Certificates**
   Tailscale admin console → **DNS** → **HTTPS Certificates** → **Enable HTTPS**.

Without both of these, `tailscale serve` cannot provision a TLS certificate for the `<machine>.<tailnet>.ts.net` name and will error.

---

## Token Requirement

MediaVault uses a shared token to protect all `/api/*` routes. The token is stored in `mvconfig.json`:

```json
{
  "web": {
    "token": "your-long-random-secret-here"
  }
}
```

**The app refuses to start bound to non-localhost addresses without a token.** Set a strong random value before exposing the console over the network.

Generate a suitable token (run in PowerShell):
```powershell
[System.Web.Security.Membership]::GeneratePassword(32, 4)
# or simpler:
-join ((65..90) + (97..122) + (48..57) | Get-Random -Count 32 | % {[char]$_})
```

---

## Setup Script

Run the provided script on the Alienware (the machine running the MediaVault app):

```powershell
.\tools\tailscale_serve_setup.ps1
# or with a custom port:
.\tools\tailscale_serve_setup.ps1 -Port 9000
```

The script:
1. Checks `tailscale` is on PATH.
2. Prints the machine's current tailnet status.
3. Reminds you to complete the admin console prerequisites and set the token.
4. Runs `tailscale serve --bg --https=443 127.0.0.1:8765` (background + persistent across reboots).
5. Prints the resulting `https://` URL and management commands.

---

## Tailscale Serve Commands

| Purpose | Command |
|---------|---------|
| Enable (background, persistent) | `tailscale serve --bg --https=443 127.0.0.1:8765` |
| Check status | `tailscale serve status` |
| Check status (JSON) | `tailscale serve status --json` |
| Turn off this mapping | `tailscale serve --https=443 127.0.0.1:8765 off` |
| Reset all serve config | `tailscale serve reset` |

The `--bg` flag makes the serve config persistent — it survives Tailscale restarts and machine reboots. Run without `--bg` for a temporary/session-only mapping.

---

## Using the Console on iPad / iPhone

1. Make sure your iPhone/iPad is connected to Tailscale (the Tailscale app must be active and logged into the same tailnet).
2. Open Safari (or any browser) and navigate to `https://<machine>.<tailnet>.ts.net`.
3. When prompted, enter the token from `mvconfig.json → web.token`. It is remembered for the browser session.
4. Alternatively, bookmark `https://<machine>.<tailnet>.ts.net?token=<yourtoken>` for one-tap access (keep this bookmark private).

---

## Security Notes

| Topic | Detail |
|-------|--------|
| **`serve` vs `funnel`** | `serve` is tailnet-only. `funnel` would expose to the public internet — we deliberately do NOT use funnel. |
| **Token guards all API routes** | Every `/api/*` request requires the `Authorization: Bearer <token>` header or `?token=` query param. |
| **Open-folder is localhost-only** | The "open in Finder" / "open folder" action only works when the request comes from `127.0.0.1` (enforced server-side), even if the API token is valid. |
| **HTTP on raw IP** | HTTP on the raw LAN or Tailscale IP is plaintext. Use the `https://<machine>.<tailnet>.ts.net` URL for sensitive sessions. |
| **TLS cert management** | Tailscale provisions and auto-renews the TLS cert. No manual certificate management is needed. |
| **Tailnet membership** | Only devices authenticated to your tailnet can reach the `ts.net` URL. Guests cannot access it. |
