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

All `/api/*` routes require a valid minted token (see [Token requirement](#token-requirement)) regardless of which access mode you use. The genuine-local (Alienware) browser is always allowed without a token.

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

MediaVault uses **admin-minted, expiring, revocable tokens** to protect all `/api/*` routes. Tokens are NOT stored in `mvconfig.json` — they live in the gitignored `mvtokens.json` (sha256-hashed; the raw token is shown once at mint and never persisted).

**Mint a token** from the Alienware (the owner's machine) — two ways:

**Option A — web Access panel (recommended):** open `http://127.0.0.1:8765` in the Alienware browser, click the **Access (🔑)** button in the header, fill in a label and TTL, click "Create token". The raw token and a ready-to-share URL are shown once — copy the URL and send it to the device.

**Option B — CLI:**
```
python main.py token create --label "iPhone" --ttl 30d
```
The command prints the raw token and the `?token=<raw>` share URL. Manage tokens:
```
python main.py token list           # see all tokens with expiry info
python main.py token revoke <id>    # revoke by id
```

**Secure by default:** with no tokens minted, the genuine-local (Alienware) browser always has full admin access — no token needed. Every remote request gets 401 until the owner mints and shares a token. The app no longer refuses to start when bound non-localhost without a token; remote is simply locked.

Available TTL options: `1h`, `8h`, `12h`, `1d`, `3d`, `7d` (default), `30d`, `never`.

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
3. Reminds you to complete the admin console prerequisites and mint a token.
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
2. On the Alienware, mint a token: `python main.py token create --label "iPhone" --ttl 30d` (or use the web Access panel). Copy the printed `?token=<raw>` share URL.
3. Open Safari on the iPhone/iPad and navigate to that share URL. The token is captured automatically, stored in a cookie, and stripped from the URL — you will not be prompted again until it expires or is revoked.
4. Tap Share → "Add to Home Screen" to install the console as a standalone PWA.
5. On future visits, open the PWA directly. If the token has expired or been revoked, a prompt appears — mint a new token and enter it, or open a fresh `?token=` share link.

---

## Security Notes

| Topic | Detail |
|-------|--------|
| **`serve` vs `funnel`** | `serve` is tailnet-only. `funnel` would expose to the public internet — we deliberately do NOT use funnel. |
| **Minted tokens guard all API routes** | Every `/api/*` request requires a valid, non-expired minted token (via `mv_token` cookie, `X-MediaVault-Token` header, or `?token=` query). Tokens are sha256-hashed in `mvtokens.json`; the raw token is shown once at mint and never stored. |
| **Genuine-local admin always allowed** | The Alienware browser (loopback host + no proxy/forwarding headers) always has full access without any token. `tailscale serve` proxies inject forwarding headers, so a proxied tailnet peer can never bypass this check. |
| **Secure by default** | With no tokens minted, remote requests get 401 immediately — the destructive console is never open to the network before the owner has set up access. |
| **Open-folder is genuine-local-admin-only** | The "open in Explorer" action only works for the genuine-local admin (loopback + no proxy headers), even if a valid token is presented. |
| **HTTP on raw IP** | HTTP on the raw LAN or Tailscale IP is plaintext. Use the `https://<machine>.<tailnet>.ts.net` URL for sensitive sessions. |
| **TLS cert management** | Tailscale provisions and auto-renews the TLS cert. No manual certificate management is needed. |
| **Tailnet membership** | Only devices authenticated to your tailnet can reach the `ts.net` URL. Guests cannot access it. |
