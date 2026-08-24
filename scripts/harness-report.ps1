param(
  [string]$Config = "D:\games\EchoWeave\config.local.json",
  [string]$FeedbackLog = "",
  [string]$Python = "D:\games\EchoWeave\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$auditLog = $cfg.harness_audit_path
if (-not $auditLog) {
  $auditLog = Join-Path $root "logs\audit.jsonl"
}
if (-not $FeedbackLog) {
  $FeedbackLog = Join-Path $root "logs\harness-feedback.jsonl"
}
$env:PYTHONPATH = "$root\src;$root\packages\echoweave_runtime\src;$root\packages\echoweave_ai\src;$root\packages\echoweave_agent_core\src;$root\packages\echoweave_coding_agent\src;$root\packages\echoweave_harness\src;$root\packages\echoweave_social\src;$root\packages\echoweave_web\src"
& $Python -m echoweave_harness.report --audit-log $auditLog --feedback-log $FeedbackLog
