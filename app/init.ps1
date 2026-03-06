param([string]$ConfigPath="config.json")
if (!(Test-Path $ConfigPath)) { Copy-Item "config.example.json" $ConfigPath }
$cfg = Get-Content $ConfigPath | ConvertFrom-Json
$dirs = @($cfg.data_dir, $cfg.files_dir, $cfg.static_dir, $cfg.log_dir)
if ($cfg.embedding_profiles) {
  foreach ($p in $cfg.embedding_profiles.PSObject.Properties) {
    $profile = $p.Value
    if ($profile.embedding_model) {
      $dirs += [System.IO.Path]::GetDirectoryName($profile.embedding_model)
    }
    if ($profile.embedding_tokenizer_dir) {
      $dirs += $profile.embedding_tokenizer_dir
    }
  }
} else {
  if ($cfg.embedding_model) {
    $dirs += [System.IO.Path]::GetDirectoryName($cfg.embedding_model)
  }
  if ($cfg.embedding_tokenizer_dir) {
    $dirs += $cfg.embedding_tokenizer_dir
  }
}
foreach ($d in $dirs) {
  if ($d -and !(Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
python .\main.py --init