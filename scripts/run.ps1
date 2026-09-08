$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Resolve-Path (Join-Path $scriptDir "..")
Set-Location $root

function Import-DotEnv($path) {
  if (-not (Test-Path $path)) { return }
  Get-Content $path | ForEach-Object {
    if ($_ -match "^\s*#" -or $_ -notmatch "=") { return }
    $name, $value = $_.Split("=", 2)
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim().Trim('"').Trim("'"), "Process")
  }
}

Import-DotEnv ".env"

$env:PYTHONPATH = $root
$env:PYTHONUNBUFFERED = "1"

$apiHost = if ($env:PSOP_REALIZE_API_HOST) { $env:PSOP_REALIZE_API_HOST } else { "127.0.0.1" }
$apiPort = if ($env:PSOP_REALIZE_API_PORT) { $env:PSOP_REALIZE_API_PORT } else { "8090" }
if (-not $env:PSOP_REALIZE_VIDEO_API_BASE_URL) {
  $env:PSOP_REALIZE_VIDEO_API_BASE_URL = "http://127.0.0.1:$apiPort"
}

$apiProcess = $null
$mcpProcess = $null

try {
  Write-Host "Starting psop-realize Video API on http://$apiHost`:$apiPort"
  $apiProcess = Start-Process python `
    -ArgumentList @("-m", "uvicorn", "app.video_main:app", "--host", $apiHost, "--port", $apiPort, "--log-level", "info") `
    -NoNewWindow `
    -PassThru

  $healthUrl = "http://127.0.0.1:$apiPort/health"
  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    if ($apiProcess.HasExited) {
      throw "psop-realize Video API exited before becoming healthy."
    }
    try {
      Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 | Out-Null
      $ready = $true
      break
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  if (-not $ready) {
    throw "psop-realize Video API did not become healthy at $healthUrl"
  }

  $mcpHost = if ($env:PSOP_REALIZE_MCP_HOST) { $env:PSOP_REALIZE_MCP_HOST } else { "0.0.0.0" }
  $mcpPort = if ($env:PSOP_REALIZE_MCP_PORT) { $env:PSOP_REALIZE_MCP_PORT } else { "8100" }
  $mcpPath = if ($env:PSOP_REALIZE_MCP_PATH) { $env:PSOP_REALIZE_MCP_PATH } else { "/mcp" }

  Write-Host "Starting psop-realize MCP Server on http://$mcpHost`:$mcpPort$mcpPath"
  $mcpProcess = Start-Process python `
    -ArgumentList @("-m", "app.mcp_server") `
    -NoNewWindow `
    -PassThru

  while (-not $apiProcess.HasExited -and -not $mcpProcess.HasExited) {
    Start-Sleep -Seconds 1
    $apiProcess.Refresh()
    $mcpProcess.Refresh()
  }
} finally {
  foreach ($process in @($mcpProcess, $apiProcess)) {
    if ($null -ne $process -and -not $process.HasExited) {
      Stop-Process -Id $process.Id -Force
    }
  }
}
