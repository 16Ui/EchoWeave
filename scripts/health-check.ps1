param(
  [string]$Config = "D:\games\EchoWeave\config.local.json",
  [switch]$Strict
)

$ErrorActionPreference = "Stop"
$checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
  param(
    [string]$Name,
    [bool]$Ok,
    [object]$Detail = $null,
    [string]$Severity = "error"
  )
  $script:checks.Add([pscustomobject]@{
    name = $Name
    ok = $Ok
    severity = $Severity
    detail = $Detail
  }) | Out-Null
}

if (-not (Test-Path -LiteralPath $Config)) {
  Add-Check "config.exists" $false "Config not found: $Config"
  $checks | ConvertTo-Json -Depth 10
  exit 1
}

$cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
Add-Check "config.exists" $true (Resolve-Path -LiteralPath $Config).Path

$base = "http://$($cfg.host):$($cfg.port)"
$token = $cfg.webhook_token
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

try {
  $health = Invoke-RestMethod "$base/healthz" -TimeoutSec 5
  Add-Check "http.healthz" ($health.ok -eq $true) $health
} catch {
  Add-Check "http.healthz" $false $_.Exception.Message
}

try {
  $admin = Invoke-RestMethod "$base/api/status" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 5
  Add-Check "http.api_status" ($admin.ok -eq $true) $admin
} catch {
  Add-Check "http.api_status" $false $_.Exception.Message
}

try {
  $login = Invoke-RestMethod "$base/api/login" -Method Post -Body (@{ token = $token } | ConvertTo-Json) -ContentType "application/json" -WebSession $session -TimeoutSec 5
  Add-Check "web.login" ($login.ok -eq $true) @{ expires_in = $login.expires_in }
} catch {
  Add-Check "web.login" $false $_.Exception.Message
}

try {
  $user = Invoke-WebRequest "$base/" -WebSession $session -TimeoutSec 5
  Add-Check "web.user_panel" ($user.StatusCode -eq 200 -and $user.Content.Contains("EchoWeave AI Coding 用户端")) @{ status = $user.StatusCode }
} catch {
  Add-Check "web.user_panel" $false $_.Exception.Message
}

try {
  $panel = Invoke-WebRequest "$base/admin" -WebSession $session -TimeoutSec 5
  Add-Check "web.admin_panel" ($panel.StatusCode -eq 200 -and $panel.Content.Contains("EchoWeave Admin 管理端")) @{ status = $panel.StatusCode }
} catch {
  Add-Check "web.admin_panel" $false $_.Exception.Message
}

Add-Check "sandbox.root_configured" ([bool]$cfg.sandbox_root) $cfg.sandbox_root "warn"
Add-Check "model.profile_configured" ([bool]$cfg.default_model_profile) $cfg.default_model_profile "warn"
if ($cfg.rag_enabled -eq $true -or "$($cfg.rag_backend)" -like "pgvector*") {
  Add-Check "rag.pgvector_dsn" ([bool]$cfg.rag_pgvector_dsn) $cfg.rag_pgvector_dsn "warn"
}
if ($cfg.onebot_api_url) {
  try {
    $onebot = Invoke-RestMethod "$($cfg.onebot_api_url)/get_status" -TimeoutSec 5
    Add-Check "onebot.api" $true $onebot "warn"
  } catch {
    Add-Check "onebot.api" $false $_.Exception.Message "warn"
  }
}

$failed = @($checks | Where-Object { -not $_.ok -and ($Strict -or $_.severity -eq "error") })
[pscustomobject]@{
  ok = ($failed.Count -eq 0)
  base_url = $base
  user_panel = "$base/"
  admin_panel = "$base/admin"
  sse = "$base/events"
  checks = $checks
} | ConvertTo-Json -Depth 12

if ($failed.Count -gt 0) {
  exit 1
}
