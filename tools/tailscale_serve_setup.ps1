#Requires -Version 5.1
<#
.SYNOPSIS
    Sets up Tailscale HTTPS serving for the MediaVault web console.

.DESCRIPTION
    Runs `tailscale serve --bg --https=443 127.0.0.1:<Port>` so the web console
    is reachable at https://<machine>.<tailnet>.ts.net from any device on your
    tailnet — without ever exposing it to the public internet (funnel is NOT used).

    The app itself binds 0.0.0.0:<Port>, so it is also reachable directly over
    http on the LAN or Tailscale IP.  Tailscale does TLS termination and
    tailnet-only routing; the app sees plain HTTP from 127.0.0.1.

    ONE-TIME ADMIN CONSOLE PREREQUISITES (must be done before this script works):
      1. In the Tailscale admin console → DNS → enable MagicDNS.
      2. In the Tailscale admin console → DNS → HTTPS Certificates → Enable HTTPS.
    Without both, tailscale serve cannot provision a cert for the ts.net DNS name.

    SECURITY — SET A TOKEN BEFORE RUNNING THIS:
      All /api/* routes are protected by a shared token stored in mvconfig.json
      under the key "web" -> "token".  Set a strong random value before you
      expose the console outside localhost:
          "web": { "token": "changeme-use-a-long-random-string", ... }
      The app refuses to start bound to non-localhost addresses without a token.

.PARAMETER Port
    Local port the MediaVault web console listens on. Defaults to 8765.

.EXAMPLE
    .\tailscale_serve_setup.ps1
    .\tailscale_serve_setup.ps1 -Port 9000

.NOTES
    To remove the mapping later:
        tailscale serve --https=443 127.0.0.1:<port> off
    To reset ALL serve config on this machine:
        tailscale serve reset
    To check current status:
        tailscale serve status
#>

[CmdletBinding()]
param(
    [int]$Port = 8765
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# 1. Verify tailscale is on PATH
# ---------------------------------------------------------------------------
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    Write-Error @"
'tailscale' was not found on PATH.
Install the Tailscale Windows client from https://tailscale.com/download/windows
and make sure it is running, then re-run this script.
"@
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Print the machine's tailnet name so the user can confirm
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Tailscale machine status ===" -ForegroundColor Cyan
try {
    tailscale status
} catch {
    Write-Warning "Could not retrieve tailscale status: $_"
}
Write-Host ""

# ---------------------------------------------------------------------------
# 3. Remind the user about prerequisites and the token
# ---------------------------------------------------------------------------
Write-Host "=== BEFORE CONTINUING — CHECK THESE ===" -ForegroundColor Yellow
Write-Host ""
Write-Host "  [ ] Admin console: DNS -> MagicDNS -> Enabled" -ForegroundColor Yellow
Write-Host "  [ ] Admin console: DNS -> HTTPS Certificates -> Enabled" -ForegroundColor Yellow
Write-Host "  [ ] mvconfig.json: web.token set to a strong random string" -ForegroundColor Yellow
Write-Host ""
Write-Host "  (The app will refuse to bind non-localhost without a token.)" -ForegroundColor DarkYellow
Write-Host ""

$confirm = Read-Host "Have you completed the above? Type 'yes' to continue, anything else to abort"
if ($confirm -ne 'yes') {
    Write-Host "Aborted. Re-run once the prerequisites are in place." -ForegroundColor Red
    exit 0
}

# ---------------------------------------------------------------------------
# 4. Expose the console via tailscale serve (HTTPS, background, persistent)
#    This maps:  https://<machine>.<tailnet>.ts.net  ->  http://127.0.0.1:<Port>
#    The app binds 0.0.0.0 (includes 127.0.0.1) so the forward works correctly.
#    'serve' is tailnet-only. 'funnel' would be public — we deliberately do NOT use it.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Enabling tailscale serve ===" -ForegroundColor Cyan
Write-Host "  Command: tailscale serve --bg --https=443 127.0.0.1:$Port"
Write-Host ""

try {
    tailscale serve --bg --https=443 "127.0.0.1:$Port"
} catch {
    Write-Error "tailscale serve failed: $_"
    exit 1
}

# ---------------------------------------------------------------------------
# 5. Retrieve and display the resulting ts.net URL
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Current serve status ===" -ForegroundColor Cyan
tailscale serve status
Write-Host ""

# Best-effort: derive the URL from `tailscale status --json`
try {
    $tsJson = tailscale status --json | ConvertFrom-Json
    $dnsName = $tsJson.Self.DNSName.TrimEnd('.')
    if ($dnsName) {
        Write-Host "Your MediaVault console is now available at:" -ForegroundColor Green
        Write-Host ""
        Write-Host "    https://$dnsName" -ForegroundColor Green
        Write-Host ""
        Write-Host "  Open that URL in Safari/Chrome on your iPad/iPhone (or any tailnet device)."
        Write-Host "  Enter the token from mvconfig.json web.token when prompted."
        Write-Host "  (Or append ?token=<yourtoken> to the URL for a one-click open.)"
    }
} catch {
    Write-Warning "Could not parse tailscale JSON status to extract DNS name: $_"
    Write-Host "Check 'tailscale serve status' above for the https:// URL." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 6. Print management commands for later reference
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Management commands ===" -ForegroundColor Cyan
Write-Host "  Status:        tailscale serve status"
Write-Host "  Turn off:      tailscale serve --https=443 127.0.0.1:$Port off"
Write-Host "  Reset all:     tailscale serve reset"
Write-Host ""
Write-Host "Remember: 'serve' keeps this tailnet-only. Never use 'funnel' for MediaVault." -ForegroundColor DarkYellow
Write-Host ""
