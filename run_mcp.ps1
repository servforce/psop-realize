$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
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

if (-not $env:OCTOPUS_VIDEO_API_BASE_URL) {
  $env:OCTOPUS_VIDEO_API_BASE_URL = "http://127.0.0.1:8090"
}

$env:PYTHONPATH = $root
python -m app.mcp_server
