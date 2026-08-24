param(
  [string]$Config = "D:\games\EchoWeave\config.local.json",
  [switch]$Strict
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot\health-check.ps1" -Config $Config -Strict:$Strict
