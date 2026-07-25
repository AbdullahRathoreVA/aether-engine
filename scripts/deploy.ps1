<#
    Initialises git, commits, and publishes the generated site to GitHub Pages.

    This script pushes PUBLIC content to the internet, so it confirms before the
    first push and refuses to force-push over existing history unless you pass
    -Force explicitly. Secrets are excluded by .gitignore, and the script hard
    -fails if it ever sees .env staged.

    Usage:
      .\scripts\deploy.ps1 -Repo aether-engine            # first time
      .\scripts\deploy.ps1                                 # subsequent commits
#>
[CmdletBinding()]
param(
    [string]$Repo = 'aether-engine',
    [string]$Message = '',
    [switch]$Force,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Warn($t) { Write-Host "    $t" -ForegroundColor Yellow }
function Die($t)  { Write-Host "`nFAILED: $t" -ForegroundColor Red; exit 1 }

# --- prerequisites ----------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is not installed. You have the installer in Downloads: Git-2.54.0-64-bit.exe"
}
$hasGh = [bool](Get-Command gh -ErrorAction SilentlyContinue)

# --- init -------------------------------------------------------------------
if (-not (Test-Path "$Root\.git")) {
    Step "Initialising repository"
    git init -b main | Out-Null
    if (-not (git config user.name))  { git config user.name  "Abdullah" }
    if (-not (git config user.email)) { git config user.email "rathoreabdullah816@gmail.com" }
}

# --- secret guard -----------------------------------------------------------
Step "Checking for secrets"
git add -A
$staged = git diff --cached --name-only
$leaks = $staged | Where-Object { $_ -match '(^|/)\.env$|\.key$|\.pem$|credentials\.json|token\.json' }
if ($leaks) {
    git reset | Out-Null
    Die "Refusing to commit - these look like secrets:`n$($leaks -join "`n")"
}
if ($staged | Where-Object { $_ -like 'state/*' }) {
    git reset | Out-Null
    Die "state/ was staged - check .gitignore before continuing."
}
Write-Host "    clean: no secrets staged" -ForegroundColor DarkGray

# --- commit -----------------------------------------------------------------
$pending = git status --porcelain
if (-not $pending) {
    Write-Host "`nNothing to commit - working tree clean." -ForegroundColor DarkGray
} else {
    if (-not $Message) {
        $pages = (Get-ChildItem "$Root\site" -Filter *.html -ErrorAction SilentlyContinue).Count
        $Message = "engine: iteration $(Get-Date -Format 'yyyy-MM-dd HH:mm') ($pages pages live)"
    }
    Step "Committing"
    git commit -m $Message | Out-Null
    Write-Host "    $Message" -ForegroundColor DarkGray
}

# --- remote -----------------------------------------------------------------
$remote = git remote get-url origin 2>$null
if (-not $remote) {
    Step "No remote configured"
    if ($hasGh) {
        Write-Host "    Creating public repo '$Repo' via gh..."
        gh repo create $Repo --public --source=. --remote=origin
        if ($LASTEXITCODE -ne 0) { Die "gh repo create failed. Run 'gh auth login' first." }
        $remote = git remote get-url origin
    } else {
        Warn "GitHub CLI (gh) not found."
        Warn "Create an empty PUBLIC repo named '$Repo' at https://github.com/new,"
        Warn "then run:  git remote add origin https://github.com/<you>/$Repo.git"
        Warn "...and re-run this script."
        exit 0
    }
}

# --- confirm before going public -------------------------------------------
if (-not $Yes) {
    Step "About to push to a PUBLIC repository"
    Write-Host "    remote : $remote"
    Write-Host "    branch : main"
    if ($Force) { Write-Host "    MODE   : FORCE (overwrites remote history)" -ForegroundColor Red }
    $ans = Read-Host "    Continue? (y/N)"
    if ($ans -notmatch '^[Yy]') { Write-Host "Aborted."; exit 0 }
}

Step "Pushing"
if ($Force) { git push -u origin main --force } else { git push -u origin main }
if ($LASTEXITCODE -ne 0) {
    Die "Push rejected. If the remote has commits you don't have, run 'git pull --rebase origin main' first."
}

# --- pages ------------------------------------------------------------------
if ($hasGh) {
    Step "Enabling GitHub Pages (main /site)"
    $slug = (gh repo view --json nameWithOwner -q .nameWithOwner 2>$null)
    if ($slug) {
        gh api -X POST "repos/$slug/pages" -f "source[branch]=main" -f "source[path]=/docs" 2>$null | Out-Null
        $user = $slug.Split('/')[0]
        $name = $slug.Split('/')[1]
        $url  = "https://$user.github.io/$name"
        Write-Host ""
        Write-Host "Site will be live in ~60s at: $url" -ForegroundColor Green
        Write-Host "Add this to your .env, then restart the engine:" -ForegroundColor Yellow
        Write-Host "    SITE_BASE_URL=$url"
    }
}

Write-Host "`nDone." -ForegroundColor Green
