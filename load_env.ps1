# load_env.ps1
# -----------------------------------------------------------------------------
# PowerShell has no built-in .env support -- this reads a .env file (KEY=VALUE
# per line, # comments and blank lines ignored) and sets each as an
# environment variable for the CURRENT session only.
#
# Usage:
#     .\load_env.ps1                  # loads .\.env
#     .\load_env.ps1 -Path .env.bot2   # loads a specific file
#
# Then run your bot in the SAME window -- env vars set this way only apply
# to the PowerShell session that ran this script.
# -----------------------------------------------------------------------------

param(
    [string]$Path = ".env"
)

if (-not (Test-Path $Path)) {
    Write-Host "ERROR: $Path not found in $(Get-Location)"
    exit 1
}

Get-Content $Path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    if ($line -match '^([^=]+)=(.*)$') {
        $key   = $matches[1].Trim()
        $value = $matches[2].Trim()
        Set-Item -Path "Env:$key" -Value $value
        Write-Host "  set $key = $value"
    }
}

Write-Host "`nLoaded $Path into this session. Now run your bot script, e.g.:"
Write-Host "  python professional_trading_bot_v45.py"
