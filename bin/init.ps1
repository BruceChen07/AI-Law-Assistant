param([string]$ConfigPath="")

$repoRoot = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $repoRoot "app"

if (-not $ConfigPath) {
  $ConfigPath = Join-Path $appDir "config.json"
}

$ConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
$configDir = Split-Path -Parent $ConfigPath
$examplePath = Join-Path $appDir "config.example.json"

if (!(Test-Path $ConfigPath)) {
  Copy-Item $examplePath $ConfigPath
}

function Resolve-ConfigItemPath([string]$Value, [string]$BaseDir) {
  if ([string]::IsNullOrWhiteSpace($Value)) {
    return ""
  }
  if ([System.IO.Path]::IsPathRooted($Value)) {
    return [System.IO.Path]::GetFullPath($Value)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $BaseDir $Value))
}

$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$dirs = @(
  Resolve-ConfigItemPath $cfg.data_dir $configDir
  Resolve-ConfigItemPath $cfg.files_dir $configDir
  Resolve-ConfigItemPath $cfg.static_dir $configDir
  Resolve-ConfigItemPath $cfg.log_dir $configDir
)

if ($cfg.embedding_profiles) {
  foreach ($p in $cfg.embedding_profiles.PSObject.Properties) {
    $profile = $p.Value
    if ($profile.embedding_model) {
      $dirs += [System.IO.Path]::GetDirectoryName((Resolve-ConfigItemPath $profile.embedding_model $configDir))
    }
    if ($profile.embedding_tokenizer_dir) {
      $dirs += Resolve-ConfigItemPath $profile.embedding_tokenizer_dir $configDir
    }
  }
} else {
  if ($cfg.embedding_model) {
    $dirs += [System.IO.Path]::GetDirectoryName((Resolve-ConfigItemPath $cfg.embedding_model $configDir))
  }
  if ($cfg.embedding_tokenizer_dir) {
    $dirs += Resolve-ConfigItemPath $cfg.embedding_tokenizer_dir $configDir
  }
}

foreach ($d in $dirs) {
  if ($d -and !(Test-Path $d)) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
  }
}

$oldAppConfig = $env:APP_CONFIG
$env:APP_CONFIG = $ConfigPath
Push-Location $repoRoot
try {
  python -m app.main --init
} finally {
  Pop-Location
  if ($null -eq $oldAppConfig) {
    Remove-Item Env:APP_CONFIG -ErrorAction SilentlyContinue
  } else {
    $env:APP_CONFIG = $oldAppConfig
  }
}
