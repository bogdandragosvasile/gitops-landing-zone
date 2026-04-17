# Bootstrap the local GitOps environment using Git Bash
# Run from PowerShell: .\bootstrap.ps1

$ErrorActionPreference = "Stop"

# Find Git Bash
$gitBash = $null
$candidates = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "$env:ProgramFiles(x86)\Git\bin\bash.exe",
    "${env:LOCALAPPDATA}\Programs\Git\bin\bash.exe"
)
foreach ($path in $candidates) {
    if (Test-Path $path) {
        $gitBash = $path
        break
    }
}

if (-not $gitBash) {
    # Try to find via where.exe, preferring git's bash over WSL
    $allBash = where.exe bash.exe 2>$null
    foreach ($path in $allBash) {
        if ($path -match "Git") {
            $gitBash = $path
            break
        }
    }
}

if (-not $gitBash) {
    Write-Error "Git Bash not found. Install Git for Windows from https://git-scm.com"
    exit 1
}

Write-Host "Using Git Bash: $gitBash" -ForegroundColor Cyan

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bootstrapScript = Join-Path $scriptDir "scripts\bootstrap.sh"

# Run bootstrap via Git Bash
& $gitBash --login -c "cd '$(($scriptDir -replace '\\','/') -replace '^([A-Za-z]):','/$1')' && bash scripts/bootstrap.sh"
