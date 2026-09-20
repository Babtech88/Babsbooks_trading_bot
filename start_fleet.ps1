# ============================================================================
# CUREX / BABSBOOKS 7-BOT FLEET LAUNCHER
# ============================================================================
# Starts all 7 trading-bot instances with unique BOT_ID values so the MRM
# can distinguish them.
#
# Usage:
#   .\start_fleet.ps1
#
# Start only selected slots:
#   .\start_fleet.ps1 -Slots 1,5
#
# Start slots 1, 3 and 6:
#   .\start_fleet.ps1 -Slots 1,3,6
#
# Stop all bots:
#   .\start_fleet.ps1 -Stop
# ============================================================================

param(
    [int[]]$Slots = @(1,2,3,4,5,6,7),

    [switch]$Stop
)

$ErrorActionPreference = "Stop"

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

$BotDirectory = $PSScriptRoot
$PythonExe = Join-Path $BotDirectory "venv\Scripts\python.exe"

$MRM_URL = "http://127.0.0.1:8800"

# IMPORTANT:
# Put your real dashboard/MRM key in an environment variable instead of
# hard-coding it here.
#
# Example:
#   $env:DASHBOARD_ACCESS_KEY = "YOUR_NEW_KEY"
#
# Or load it from your own .env file before running this script.

if (-not $env:DASHBOARD_ACCESS_KEY) {
    Write-Host ""
    Write-Host "WARNING: DASHBOARD_ACCESS_KEY is not set." -ForegroundColor Yellow
    Write-Host "Set it before starting the fleet if your MRM requires it." -ForegroundColor Yellow
    Write-Host ""
}

# ----------------------------------------------------------------------------
# Seven bot definitions
# ----------------------------------------------------------------------------

$Bots = @{
    1 = @{
        Name       = "V46.1 Mechanical Mind Gold"
        BotId      = "bot_1_v46gold"
        Script     = "simple_trade_bot_v46_gold.py"
        SymbolMode = ""
    }

    2 = @{
        Name       = "V45 7-Gate Mechanical"
        BotId      = "bot_2_v45"
        Script     = "professional_trading_bot_v45.py"
        SymbolMode = ""
    }

    3 = @{
        Name       = "V36 Complete HQT DCA"
        BotId      = "bot_3_v36hqt"
        Script     = "babsbooks_trading_bot_v36.py"
        SymbolMode = ""
    }

    4 = @{
        Name       = "V36 Complete No HQT"
        BotId      = "bot_4_v36nohqt"
        Script     = "babsbooks_trading_bot_v36_no_hqt.py"
        SymbolMode = ""
    }

    5 = @{
        Name       = "V46 Confluence"
        BotId      = "bot_5_v46conf"
        Script     = "v46_confluence_bot.py"
        SymbolMode = ""
    }

    6 = @{
        Name       = "V36 ITAFX"
        BotId      = "bot_6_v36itafx"
        Script     = "babsbooks_trading_bot_v36_itafx.py"
        SymbolMode = ""
    }

    7 = @{
        Name       = "XAUUSD Only"
        BotId      = "bot_7_xauusd"
        Script     = "simple_trade_bot_v46_gold.py"
        SymbolMode = "xauusd_only"
    }
}

# ----------------------------------------------------------------------------
# Stop existing fleet
# ----------------------------------------------------------------------------

if ($Stop) {

    Write-Host ""
    Write-Host "Stopping trading-bot fleet..." -ForegroundColor Yellow
    Write-Host ""

    $BotScriptNames = @(
        "simple_trade_bot_v46_gold.py",
        "professional_trading_bot_v45.py",
        "babsbooks_trading_bot_v36.py",
        "babsbooks_trading_bot_v36_no_hqt.py",
        "v46_confluence_bot.py",
        "babsbooks_trading_bot_v36_itafx.py"
    )

    $Processes = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match "python" -and
            $_.CommandLine -and
            (
                $BotScriptNames | Where-Object {
                    $_.CommandLine -like "*$_*"
                }
            )
        }

    foreach ($Process in $Processes) {
        Write-Host "Stopping PID $($Process.ProcessId)" -ForegroundColor Red
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "Fleet stopped." -ForegroundColor Green
    exit
}

# ----------------------------------------------------------------------------
# Check Python
# ----------------------------------------------------------------------------

if (-not (Test-Path $PythonExe)) {

    Write-Host ""
    Write-Host "ERROR: Python virtual environment was not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Expected:"
    Write-Host $PythonExe
    Write-Host ""
    Write-Host "Make sure your venv exists in the trading_bot folder."
    exit 1
}

# ----------------------------------------------------------------------------
# Check MRM
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "              7-BOT TRADING FLEET LAUNCHER" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Bot directory : $BotDirectory"
Write-Host "Python        : $PythonExe"
Write-Host "MRM URL       : $MRM_URL"
Write-Host ""

# ----------------------------------------------------------------------------
# Start selected bots
# ----------------------------------------------------------------------------

foreach ($Slot in $Slots) {

    if (-not $Bots.ContainsKey($Slot)) {
        Write-Host "ERROR: Invalid slot: $Slot" -ForegroundColor Red
        continue
    }

    $Bot = $Bots[$Slot]

    $ScriptPath = Join-Path $BotDirectory $Bot.Script

    Write-Host "------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "Slot       : $Slot" -ForegroundColor Cyan
    Write-Host "Name       : $($Bot.Name)" -ForegroundColor White
    Write-Host "BOT_ID     : $($Bot.BotId)" -ForegroundColor Yellow
    Write-Host "Script     : $($Bot.Script)" -ForegroundColor White
    Write-Host "MRM        : $MRM_URL" -ForegroundColor White

    if ($Bot.SymbolMode) {
        Write-Host "Symbol Mode: $($Bot.SymbolMode)" -ForegroundColor Magenta
    }

    if (-not (Test-Path $ScriptPath)) {
        Write-Host "ERROR: Script not found!" -ForegroundColor Red
        Write-Host "       $ScriptPath" -ForegroundColor Red
        continue
    }

    # ------------------------------------------------------------
    # Build environment variables for this specific bot
    # ------------------------------------------------------------

    $BotEnvironment = @(
        "BOT_ID=$($Bot.BotId)"
        "MRM_URL=$MRM_URL"
    )

    if ($Bot.SymbolMode) {
        $BotEnvironment += "SYMBOL_MODE=$($Bot.SymbolMode)"
    }
    else {
        $BotEnvironment += "SYMBOL_MODE="
    }

    if ($env:DASHBOARD_ACCESS_KEY) {
        $BotEnvironment += "DASHBOARD_ACCESS_KEY=$env:DASHBOARD_ACCESS_KEY"
    }

    # ------------------------------------------------------------
    # Create command that sets environment variables and starts bot
    # ------------------------------------------------------------

    $EnvironmentCommands = ""

    foreach ($EnvironmentVariable in $BotEnvironment) {

        $Parts = $EnvironmentVariable -split "=", 2

        $VariableName = $Parts[0]
        $VariableValue = ""

        if ($Parts.Count -gt 1) {
            $VariableValue = $Parts[1]
        }

        # Escape PowerShell single quotes
        $VariableValue = $VariableValue.Replace("'", "''")

        $EnvironmentCommands += "`$env:$VariableName = '$VariableValue'; "
    }

    $Command = @"
Set-Location -LiteralPath '$BotDirectory';
$EnvironmentCommands
Write-Host '';
Write-Host '==================================================' -ForegroundColor Cyan;
Write-Host ' BOT SLOT $Slot STARTED' -ForegroundColor Green;
Write-Host ' BOT_ID: $($Bot.BotId)' -ForegroundColor Yellow;
Write-Host ' SCRIPT: $($Bot.Script)' -ForegroundColor White;
Write-Host ' MRM: $MRM_URL' -ForegroundColor White;
Write-Host '==================================================' -ForegroundColor Cyan;
Write-Host '';
& '$PythonExe' '$ScriptPath';
Write-Host '';
Write-Host 'BOT PROCESS EXITED' -ForegroundColor Red;
Write-Host 'BOT_ID: $($Bot.BotId)' -ForegroundColor Yellow;
Write-Host '';
Read-Host 'Press ENTER to close this bot window'
"@

    # ------------------------------------------------------------
    # Start a separate PowerShell window
    # ------------------------------------------------------------

    Start-Process powershell.exe `
        -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $Command

    Write-Host "Started." -ForegroundColor Green

    # Small delay prevents all 7 processes from initializing at exactly
    # the same instant.
    Start-Sleep -Seconds 2
}

# ----------------------------------------------------------------------------
# Finished
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "                  FLEET LAUNCH COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

Write-Host "Running slots:" -ForegroundColor Cyan

foreach ($Slot in $Slots) {

    if ($Bots.ContainsKey($Slot)) {

        $Bot = $Bots[$Slot]

        Write-Host (
            "  Slot {0} -> {1} -> {2}" -f `
            $Slot,
            $Bot.BotId,
            $Bot.Script
        )
    }
}

Write-Host ""
Write-Host "To stop the fleet:" -ForegroundColor Yellow
Write-Host "  .\start_fleet.ps1 -Stop"
Write-Host ""