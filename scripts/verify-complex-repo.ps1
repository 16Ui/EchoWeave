param(
  [string]$Target = ".",
  [string]$Python = "python"
)

$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "$root\src;$root\packages\echoweave_runtime\src;$root\packages\echoweave_ai\src;$root\packages\echoweave_agent_core\src;$root\packages\echoweave_coding_agent\src;$root\packages\echoweave_harness\src;$root\packages\echoweave_social\src;$root\packages\echoweave_web\src"

& $Python -m echoweave_coding_agent.cli complex-repo-verify --target $Target --json
exit $LASTEXITCODE
