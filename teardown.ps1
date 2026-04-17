# Teardown the local GitOps environment using Git Bash
# Run from PowerShell: .\teardown.ps1

$ErrorActionPreference = "Stop"

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
    $allBash = where.exe bash.exe 2>$null
    foreach ($path in $allBash) {
        if ($path -match "Git") {
            $gitBash = $path
            break
        }
    }
}

if (-not $gitBash) {
    Write-Error "Git Bash not found."
    exit 1
}

Write-Host "Using Git Bash: $gitBash" -ForegroundColor Cyan

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

& $gitBash --login -c "cd '$(($scriptDir -replace '\\','/') -replace '^([A-Za-z]):','/$1')' && bash scripts/teardown.sh"
