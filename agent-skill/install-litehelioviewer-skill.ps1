param(
    [string]$Destination = "$env:USERPROFILE\.codex\skills\litehelioviewer-control"
)

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "litehelioviewer-control"

if (!(Test-Path $source)) {
    throw "Skill source not found: $source"
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Copy-Item -Path (Join-Path $source "*") -Destination $Destination -Recurse -Force
Write-Host "Installed litehelioviewer-control skill to $Destination"
