<#
    Put a secret into .env without it ever appearing on screen, in your shell
    history, or in a chat log.

    .env is gitignored and never leaves this machine - verified against the
    full git history, not just the current commit.

    Usage:
      .\scripts\set-secret.ps1 GROQ_API_KEY
      .\scripts\set-secret.ps1 CHECKOUT_URL -Plain    # not secret, show it

    Then restart the engine. That is the whole process.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Name,
    [switch]$Plain
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root '.env'

if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root '.env.example') $EnvFile
    Write-Host "Created .env from template." -ForegroundColor DarkGray
}

# Refuse to run if .env is not actually protected - better to stop than to
# write a key into a file that git would happily publish.
Push-Location $Root
$ignored = (git check-ignore .env 2>$null)
Pop-Location
if (-not $ignored) {
    Write-Host "REFUSING: .env is not gitignored. Fix .gitignore first." -ForegroundColor Red
    exit 1
}

if ($Plain) {
    $value = Read-Host "Enter value for $Name"
} else {
    $secure = Read-Host "Paste $Name (input is hidden)" -AsSecureString
    $value  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}

if ([string]::IsNullOrWhiteSpace($value)) {
    Write-Host "Nothing entered - no change made." -ForegroundColor Yellow
    exit 0
}

# Strip whitespace/newlines. A trailing newline on a pasted key produces
# baffling "invalid credential" errors that look like a bad key.
$value = $value.Trim()

$lines = @(Get-Content $EnvFile -ErrorAction SilentlyContinue)
$found = $false
$out = foreach ($line in $lines) {
    if ($line -match "^\s*$([regex]::Escape($Name))=") {
        $found = $true
        "$Name=$value"
    } else {
        $line
    }
}
if (-not $found) { $out += "$Name=$value" }

Set-Content -Path $EnvFile -Value $out -Encoding utf8

$shown = if ($Plain) { $value } else { "$($value.Substring(0, [Math]::Min(4, $value.Length)))****  ($($value.Length) chars)" }
Write-Host "`nSaved $Name = $shown" -ForegroundColor Green
Write-Host ".env is gitignored - this will not reach GitHub." -ForegroundColor DarkGray
Write-Host "`nRestart the engine to pick it up:  .\scripts\run.ps1" -ForegroundColor Cyan
