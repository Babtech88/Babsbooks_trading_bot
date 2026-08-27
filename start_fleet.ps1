# start_fleet.ps1
# -----------------------------------------------------------------------------
# Starts the Master Risk Manager and every bot instance as background
# processes, each writing its own log file. Run this from the folder
# containing master_risk_manager.py, risk_client.py, bot_risk_config.py,
# and all the wired bot scripts.
#
# Usage (PowerShell, run as the same user MT5 is logged in under):
#     .\start_fleet.ps1
#
# To change which bots run / their symbol mode, edit the $Bots array below.
# -----------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
$RootDir = $PSScriptRoot
Set-Location $RootDir

$LogDir = Join-Path $RootDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

Write-Host "=============================================================="
Write-Host " BabsBooks Fleet Launcher"
Write-Host "=============================================================="

# ── 1. Start the Master Risk Manager ─────────────────────────────────────────
Write-Host "`n[1/2] Starting Master Risk Manager on 127.0.0.1:8800 ..."

$mrmLog = Join-Path $LogDir "mrm.log"
$mrmProc = Start-Process -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "master_risk_manager:app", "--host", "127.0.0.1", "--port", "8800" `
    -RedirectStandardOutput $mrmLog `
    -RedirectStandardError (Join-Path $LogDir "mrm_error.log") `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 3

# Health check
try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8800/v1/status" -Method Get -TimeoutSec 5
    Write-Host "      MRM is up. PID=$($mrmProc.Id)"
} catch {
    Write-Host "      ERROR: MRM did not respond. Check $mrmLog and $LogDir\mrm_error.log"
    exit 1
}

# ── 2. Start each bot instance ───────────────────────────────────────────────
# Format: BotId, ScriptFile, SymbolMode ("" = default/free trading, "xauusd_only" = gold-only slot)
$Bots = @(
    @{ Id = "bot_1_v46gold";     Script = "simple_trade_bot_v46_gold.py";          SymbolMode = "" },
    @{ Id = "bot_2_v45";         Script = "professional_trading_bot_v45.py";       SymbolMode = "" },
    @{ Id = "bot_3_v36hqt";      Script = "babsbooks_trading_bot_v36.py";          SymbolMode = "" },
    @{ Id = "bot_4_v36nohqt";    Script = "babsbooks_trading_bot_v36_no_hqt.py";   SymbolMode = "" },
    @{ Id = "bot_5_v46conf";     Script = "v46_confluence_bot.py";                 SymbolMode = "" },
    @{ Id = "bot_6_v36itafx";    Script = "babsbooks_trading_bot_v36_itafx.py";    SymbolMode = "" },
    @{ Id = "bot_7_xauusd";      Script = "simple_trade_bot_v46_gold.py";          SymbolMode = "xauusd_only" }
)

Write-Host "`n[2/2] Starting $($Bots.Count) bot instance(s) ..."

$BotProcs = @()

foreach ($bot in $Bots) {
    $scriptPath = Join-Path $RootDir $bot.Script
    if (-not (Test-Path $scriptPath)) {
        Write-Host "      SKIP  $($bot.Id) -- script not found: $($bot.Script)"
        continue
    }

    $env:BOT_ID     = $bot.Id
    $env:MRM_URL    = "http://127.0.0.1:8800"
    if ($bot.SymbolMode -ne "") {
        $env:SYMBOL_MODE = $bot.SymbolMode
    } else {
        Remove-Item Env:\SYMBOL_MODE -ErrorAction SilentlyContinue
    }

    $botLog = Join-Path $LogDir "$($bot.Id).log"
    $botErr = Join-Path $LogDir "$($bot.Id)_error.log"

    $proc = Start-Process -FilePath "python" `
        -ArgumentList $bot.Script `
        -RedirectStandardOutput $botLog `
        -RedirectStandardError $botErr `
        -WindowStyle Hidden `
        -PassThru

    $tag = if ($bot.SymbolMode -ne "") { " [$($bot.SymbolMode)]" } else { "" }
    Write-Host "      STARTED  $($bot.Id)$tag  PID=$($proc.Id)  -> $botLog"
    $BotProcs += $proc

    Start-Sleep -Seconds 2   # stagger starts so they don't all hit MT5/rates at once
}

Write-Host "`n=============================================================="
Write-Host " Fleet is running. MRM PID=$($mrmProc.Id)  |  $($BotProcs.Count) bot(s) started"
Write-Host " Logs: $LogDir"
Write-Host " To stop everything, run: .\stop_fleet.ps1"
Write-Host "=============================================================="

# Save PIDs so stop_fleet.ps1 can find them
$allPids = @{ mrm = $mrmProc.Id; bots = $BotProcs.Id }
$allPids | ConvertTo-Json | Out-File (Join-Path $RootDir "fleet_pids.json")
