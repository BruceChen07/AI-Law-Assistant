param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 5173,
  [switch]$SkipLlamaCpp,
  [string]$LlamaCppHost = "127.0.0.1",
  [int]$LlamaCppPort = 18080,
  [int]$LlamaCppCtxSize = 16384,
  [int]$LlamaCppThreads = 0,
  [string]$LlamaCppModelPath = "",
  # Idle seconds after which llama.cpp unloads the model (frees RAM).
  # -1 = read llm_config.sleep_idle_seconds from config; 0 = never sleep.
  [int]$LlamaCppSleepIdleSeconds = -1
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$runtimeDir = Join-Path $repoRoot ".runtime"
$pidFile = Join-Path $runtimeDir "services.pids.json"
$logDir = Join-Path $runtimeDir "logs"
$modelsDir = Join-Path $repoRoot "models\llm"
$webRoot = Join-Path $repoRoot "web"

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if (Test-Path $pidFile) {
  $existing = Get-Content $pidFile -Raw | ConvertFrom-Json
  $alive = @()
  foreach ($p in @($existing.backend_pid, $existing.frontend_pid, $existing.llamacpp_pid)) {
    if ($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) { $alive += $p }
  }
  if ($alive.Count -gt 0) {
    Write-Host "Services seem running. Use stop-services.ps1 first. Alive PIDs: $($alive -join ', ')"
    exit 1
  } else {
    Remove-Item $pidFile -Force
  }
}

function Get-BackendPython {
  $candidates = @(
    (Join-Path $repoRoot "app\.venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) { return $candidate }
  }
  return $null
}

function Get-NpmExe {
  foreach ($name in @("npm.cmd", "npm")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }
  }
  return $null
}

function Read-AppConfig {
  $configPath = $env:APP_CONFIG
  if (-not $configPath) { $configPath = Join-Path $repoRoot "app\config.json" }
  if (-not (Test-Path $configPath)) { return $null }
  # config.json is UTF-8; -Encoding UTF8 is required because Windows PowerShell 5.1
  # otherwise reads it with the ANSI codepage and fails to parse non-ASCII content.
  return (Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Is-LlamaCppProvider($cfg) {
  if (-not $cfg) { return $false }
  if (-not $cfg.llm_config) { return $false }
  return ($cfg.llm_config.provider -as [string]).Trim().ToLowerInvariant() -eq "llama_cpp"
}

function Test-LlamaCppReady([string]$HostName, [int]$port) {
  try {
    $uri = "http://$HostName`:$port/v1/models"
    $resp = Invoke-WebRequest -Uri $uri -Method GET -TimeoutSec 5 -UseBasicParsing
    return $resp.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Find-LlamaServerExe {
  $candidates = @(
    "llama-server.exe",
    "llama-server",
    (Join-Path $repoRoot "third_party\llama.cpp\bin-win-cpu-x64\llama-server.exe"),
    (Join-Path $repoRoot "third_party\llama.cpp\bin-win-cpu-x64\llama-server"),
    (Join-Path $repoRoot "third_party\llama.cpp\build\bin\Release\llama-server.exe"),
    (Join-Path $repoRoot "third_party\llama.cpp\build\bin\llama-server.exe"),
    (Join-Path $repoRoot "third_party\llama.cpp\build\bin\llama-server")
  )
  foreach ($item in $candidates) {
    $cmd = Get-Command $item -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }
    if (Test-Path $item) { return (Resolve-Path $item).Path }
  }
  return $null
}

function Get-GgufModels([string]$dir) {
  if (-not (Test-Path $dir)) { return @() }
  return Get-ChildItem -Path $dir -Filter *.gguf -File -ErrorAction SilentlyContinue | Sort-Object Length -Descending
}

function Select-GgufModel($candidates) {
  Write-Host ""
  Write-Host "Select model for llama.cpp server:"
  for ($i = 0; $i -lt $candidates.Count; $i++) {
    $p = $candidates[$i]
    $sizeMb = [Math]::Round($p.Length / 1MB, 0)
    Write-Host ("  [{0}] {1}  ({2} MB)" -f ($i + 1), $p.Name, $sizeMb)
  }
  Write-Host "  [0] Skip (do not start llama.cpp)"

  while ($true) {
    $choiceText = Read-Host ("Select [0-{0}]" -f $candidates.Count)
    $choice = 0
    if ([int]::TryParse($choiceText, [ref]$choice)) {
      if ($choice -eq 0) { return $null }
      if ($choice -ge 1 -and $choice -le $candidates.Count) { return $candidates[$choice - 1] }
    }
    Write-Host ("Please enter a number between 0 and {0}." -f $candidates.Count)
  }
}

$cfg = Read-AppConfig
$llamacppProc = $null
$llamacppLog = Join-Path $logDir "llamacpp-server.log"

if ($SkipLlamaCpp) {
  Write-Host "[SKIP] llama.cpp startup skipped (-SkipLlamaCpp)."
} elseif ($cfg -and (Is-LlamaCppProvider $cfg)) {
  $serverExe = Find-LlamaServerExe
  if (-not $serverExe) {
    Write-Host "[WARN] llama-server.exe not found. Skipping llama.cpp startup."
  } elseif (Test-LlamaCppReady -HostName $LlamaCppHost -port $LlamaCppPort) {
    Write-Host ("[OK] llama.cpp already running at {0}:{1}" -f $LlamaCppHost, $LlamaCppPort)
  } else {
    $modelPath = $null
    if ($LlamaCppModelPath) {
      if (-not (Test-Path $LlamaCppModelPath)) {
        Write-Host ("[WARN] GGUF model not found: {0}. Skipping llama.cpp startup." -f $LlamaCppModelPath)
      } else {
        $modelPath = (Resolve-Path $LlamaCppModelPath).Path
      }
    } else {
      $candidates = @(Get-GgufModels -dir $modelsDir)
      if ($candidates.Count -eq 0) {
        Write-Host ("[WARN] No GGUF files found in {0}. Skipping llama.cpp startup." -f $modelsDir)
      } else {
        $selected = Select-GgufModel $candidates
        if ($selected) { $modelPath = $selected.FullName }
        if (-not $selected) { Write-Host "[SKIP] llama.cpp startup skipped by user." }
      }
    }

    if ($modelPath) {
      $threads = $LlamaCppThreads
      if (-not $threads -or $threads -le 0) {
        $threads = [Math]::Max(4, [Environment]::ProcessorCount - 2)
      }
      $ctxSize = [Math]::Max(1024, $LlamaCppCtxSize)
      $alias = [System.IO.Path]::GetFileNameWithoutExtension($modelPath)

      # Idle unload: releases the model's RAM when no task runs, and reloads it on
      # the next request (first request after idling pays a cold-start cost).
      $sleepIdleSeconds = $LlamaCppSleepIdleSeconds
      if ($sleepIdleSeconds -lt 0) {
        $sleepIdleSeconds = 1200
        if ($cfg -and $cfg.llm_config -and $cfg.llm_config.sleep_idle_seconds -ne $null) {
          $sleepIdleSeconds = [int]$cfg.llm_config.sleep_idle_seconds
        }
      }
      if ($sleepIdleSeconds -lt 0) { $sleepIdleSeconds = 0 }

      $llamaArgs = @(
        "--host", $LlamaCppHost,
        "--port", "$LlamaCppPort",
        "--model", $modelPath,
        "--alias", $alias,
        "--ctx-size", "$ctxSize",
        "--threads", "$([Math]::Max(1, $threads))",
        "--threads-http", "2",
        "--batch-size", "1024",
        "--ubatch-size", "512",
        "--parallel", "1",
        "--n-gpu-layers", "0",
        "--jinja",
        "--cont-batching",
        "--metrics"
      )
      if ($sleepIdleSeconds -gt 0) {
        $llamaArgs += @("--sleep-idle-seconds", "$sleepIdleSeconds")
      }

      Write-Host ""
      Write-Host "[START] llama.cpp server"
      Write-Host ("  Model: {0}" -f ([System.IO.Path]::GetFileName($modelPath)))
      Write-Host ("  Host:  http://{0}:{1}" -f $LlamaCppHost, $LlamaCppPort)
      Write-Host ("  Alias: {0}" -f $alias)
      if ($sleepIdleSeconds -gt 0) {
        Write-Host ("  Idle unload: {0}s without requests releases model memory" -f $sleepIdleSeconds)
      } else {
        Write-Host "  Idle unload: disabled (model stays resident)"
      }

      $llamacppErrLog = Join-Path $logDir "llamacpp-server.err.log"
      $llamacppProc = Start-Process -FilePath $serverExe -ArgumentList $llamaArgs -WorkingDirectory $repoRoot -PassThru -RedirectStandardOutput $llamacppLog -RedirectStandardError $llamacppErrLog
      Write-Host ("  PID: {0}" -f $llamacppProc.Id)
      Write-Host ("  Log: {0}" -f $llamacppLog)
      Write-Host "  Waiting for server to be ready ..." -NoNewline
      $deadline = (Get-Date).AddSeconds(300)
      $ready = $false
      while ((Get-Date) -lt $deadline) {
        if (Test-LlamaCppReady -HostName $LlamaCppHost -port $LlamaCppPort) {
          $ready = $true
          break
        }
        Start-Sleep -Seconds 2
      }
      Write-Host (" {0}" -f ($(if ($ready) { "READY" } else { "TIMEOUT" })))
      if (-not $ready) {
        Write-Host "[WARN] llama.cpp did not become ready in time. Continuing anyway."
      }
    }
  }
} elseif ($cfg) {
  $provider = ""
  if ($cfg.llm_config -and $cfg.llm_config.provider) { $provider = $cfg.llm_config.provider }
  Write-Host ("[INFO] Provider is '{0}', not llama_cpp. Skipping llama.cpp startup." -f $provider)
} else {
  Write-Host "[WARN] No config.json found. Skipping llama.cpp startup."
}

$pythonExe = Get-BackendPython
if (-not $pythonExe) {
  Write-Host "Virtual environment python not found."
  Write-Host "Please create one, e.g. python -m venv app\.venv"
  exit 1
}

$npmExe = Get-NpmExe
if (-not $npmExe) {
  Write-Host "npm not found in PATH. Please install Node.js."
  exit 1
}

$backendLog = Join-Path $logDir "backend.log"
$frontendLog = Join-Path $logDir "frontend.log"

$oldAppPort = $env:APP_PORT
$env:APP_PORT = "$BackendPort"
$backendErrLog = Join-Path $logDir "backend.err.log"
$backendProc = Start-Process -FilePath $pythonExe -ArgumentList @("-m", "app.main") -WorkingDirectory $repoRoot -PassThru -RedirectStandardOutput $backendLog -RedirectStandardError $backendErrLog
if ($null -ne $oldAppPort) { $env:APP_PORT = $oldAppPort } else { Remove-Item Env:APP_PORT -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 800

$frontendArgs = @("run", "dev", "--", "--host", "0.0.0.0", "--port", "$FrontendPort")
$frontendErrLog = Join-Path $logDir "frontend.err.log"
$frontendProc = Start-Process -FilePath $npmExe -ArgumentList $frontendArgs -WorkingDirectory $webRoot -PassThru -RedirectStandardOutput $frontendLog -RedirectStandardError $frontendErrLog

$data = [ordered]@{
  backend_pid = $backendProc.Id
  frontend_pid = $frontendProc.Id
  backend_port = $BackendPort
  frontend_port = $FrontendPort
  started_at = [DateTime]::UtcNow.ToString("o")
  backend_log = $backendLog
  frontend_log = $frontendLog
}

if ($llamacppProc) {
  $data.llamacpp_pid = $llamacppProc.Id
  $data.llamacpp_port = $LlamaCppPort
  $data.llamacpp_log = $llamacppLog
}

$json = $data | ConvertTo-Json -Depth 5

Set-Content -Path $pidFile -Value $json -Encoding UTF8

Write-Host ""
Write-Host ("Started backend PID: {0} at http://localhost:{1}" -f $backendProc.Id, $BackendPort)
Write-Host ("Started frontend PID: {0} at http://localhost:{1}" -f $frontendProc.Id, $FrontendPort)
if ($llamacppProc) {
  Write-Host ("Started llama.cpp PID: {0} at http://{1}:{2}" -f $llamacppProc.Id, $LlamaCppHost, $LlamaCppPort)
}
Write-Host "PID file: $pidFile"
