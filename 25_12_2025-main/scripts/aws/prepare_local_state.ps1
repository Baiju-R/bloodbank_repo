param(
    [string]$ProjectRoot = "."
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

if (!(Test-Path "data")) { New-Item -ItemType Directory -Path "data" | Out-Null }
if (!(Test-Path "media")) { New-Item -ItemType Directory -Path "media" | Out-Null }
if (!(Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

if (Test-Path "db.sqlite3") {
    Copy-Item "db.sqlite3" "data/db.sqlite3" -Force
    Write-Host "Copied db.sqlite3 -> data/db.sqlite3"
} elseif (!(Test-Path "data/db.sqlite3")) {
    throw "No db.sqlite3 found at project root or data/db.sqlite3"
}

if (!(Test-Path ".env")) {
    Write-Warning ".env not found. Create .env before deployment (SECRET_KEY, ALLOWED_HOSTS, AWS settings)."
}

Write-Host "Local state prepared: data/, media/, logs/"
