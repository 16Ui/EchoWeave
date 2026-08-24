param(
  [string]$Config = "D:\games\EchoWeave\config.local.json",
  [string]$Python = "D:\games\EchoWeave\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "$root\src;$root\packages\echoweave_runtime\src;$root\packages\echoweave_ai\src;$root\packages\echoweave_agent_core\src;$root\packages\echoweave_coding_agent\src;$root\packages\echoweave_harness\src;$root\packages\echoweave_social\src;$root\packages\echoweave_web\src"
Write-Host "Starting EchoWeave with config: $Config"
& $Python -m echoweave.cli webhook --config $Config
