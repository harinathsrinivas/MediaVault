$envLine = Get-Content "C:\Users\harin\PycharmProjects\MediaVault\.claude\.env" | Where-Object { $_ -match '^github_token=' }
$token = ($envLine -split '=', 2)[1].Trim()
if (-not $token) { throw "Token not found in .claude\.env" }
if (-not $token.StartsWith("github_pat_")) { throw "Token does not start with github_pat_" }
Write-Host ("Token length: " + $token.Length)
Write-Host ("Token prefix: " + $token.Substring(0, 11) + "...")
Write-Host "PASS: Token read successfully"
