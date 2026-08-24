param(
  [string]$Config = "D:\games\EchoWeave\config.local.json",
  [string]$Python = "D:\games\EchoWeave\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
if (-not $cfg.rag_pgvector_dsn) {
  throw "rag_pgvector_dsn is missing in $Config"
}
$env:PYTHONPATH = "$root\src;$root\packages\echoweave_runtime\src;$root\packages\echoweave_ai\src;$root\packages\echoweave_agent_core\src;$root\packages\echoweave_coding_agent\src;$root\packages\echoweave_harness\src;$root\packages\echoweave_social\src;$root\packages\echoweave_web\src"
& $Python -m echoweave_runtime.rag.init_db --dsn $cfg.rag_pgvector_dsn --table $cfg.rag_pgvector_table
