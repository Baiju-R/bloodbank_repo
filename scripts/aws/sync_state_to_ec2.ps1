param(
    [Parameter(Mandatory = $true)][string]$Ec2Host,
    [Parameter(Mandatory = $true)][string]$KeyPath,
    [string]$User = "ubuntu",
    [string]$RemoteDir = "~/bloodbridge"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("Command failed ({0}): {1}" -f $LASTEXITCODE, ($File + " " + ($Arguments -join " ")))
    }
}

if (!(Test-Path $KeyPath)) {
    Write-Error ("PEM key file not found " + $KeyPath)
    exit 1
}

if (!(Test-Path "docker-compose.aws.sqlite.yml")) {
    throw "docker-compose.aws.sqlite.yml not found. Run command from project root."
}

$ResolvedKeyPath = (Resolve-Path $KeyPath).Path
$SshTarget = ("{0}@{1}" -f $User, $Ec2Host)
$ScpTargetPrefix = ("{0}:{1}" -f $SshTarget, $RemoteDir)

Invoke-External -File "ssh" -Arguments @(
    "-i", $ResolvedKeyPath,
    $SshTarget,
    "mkdir -p $RemoteDir/data $RemoteDir/media $RemoteDir/logs"
)

Invoke-External -File "scp" -Arguments @(
    "-i", $ResolvedKeyPath,
    "docker-compose.aws.sqlite.yml",
    ($ScpTargetPrefix + "/docker-compose.aws.sqlite.yml")
)

if (Test-Path ".env") {
    Invoke-External -File "scp" -Arguments @(
        "-i", $ResolvedKeyPath,
        ".env",
        ($ScpTargetPrefix + "/.env")
    )
}

if (!(Test-Path "data/db.sqlite3")) {
    throw "data/db.sqlite3 not found. Run scripts/aws/prepare_local_state.ps1 first."
}

Invoke-External -File "scp" -Arguments @(
    "-i", $ResolvedKeyPath,
    "data/db.sqlite3",
    ($ScpTargetPrefix + "/data/db.sqlite3")
)

if (Test-Path "media") {
    Invoke-External -File "scp" -Arguments @(
        "-i", $ResolvedKeyPath,
        "-r",
        "media",
        ($ScpTargetPrefix + "/")
    )
}

if (Test-Path "logs") {
    Invoke-External -File "scp" -Arguments @(
        "-i", $ResolvedKeyPath,
        "-r",
        "logs",
        ($ScpTargetPrefix + "/")
    )
}

Write-Host ("State sync complete to {0} {1}" -f $SshTarget, $RemoteDir)
