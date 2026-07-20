$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$version = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$parts = $version.Split('.')
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
  throw "Python 3.11+ is required. Current python is $version. Use WSL with Python 3.11+ or install Python 3.11+ on Windows."
}

if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    if ($_ -match "^\s*#" -or $_ -notmatch "=") { return }
    $name, $value = $_.Split("=", 2)
    if (-not [Environment]::GetEnvironmentVariable($name)) {
      [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim().Trim('"').Trim("'"), "Process")
    }
  }
}

$env:PYTHONPATH = $root
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --reload
