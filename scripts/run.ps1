<#
    Starts the Aether Engine daemon.
    Usage:  .\scripts\run.ps1  [-NoBrowser]
#>
[CmdletBinding()]
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# --- python present? --------------------------------------------------------
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue) }
if (-not $py) {
    Write-Host "Python not found on PATH." -ForegroundColor Red
    Write-Host "Install from https://python.org (tick 'Add to PATH'), then re-run."
    exit 1
}

$ver = & $py.Source --version
Write-Host "Using $ver" -ForegroundColor DarkGray

# --- first-run scaffolding --------------------------------------------------
if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
    Write-Host ""
    Write-Host "Created .env from the template." -ForegroundColor Yellow
    Write-Host "The engine will start, but CHECKOUT_URL is empty - so it will"
    Write-Host "generate traffic into a dead end until you fill that in."
    Write-Host ""
}

$args = @('-m', 'engine.main')
if ($NoBrowser) { $args += '--no-browser' }

Write-Host "Starting Aether Engine... (Ctrl-C to stop)" -ForegroundColor Cyan
& $py.Source @args
