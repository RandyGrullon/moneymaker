# ============================================================
# Arranca el bot y lo REINICIA solo si se cae.
# Sirve igual en local que en un VPS.
#   Uso manual:  powershell -ExecutionPolicy Bypass -File scripts\start_bot.ps1
#   En VPS: se instala como tarea programada con install_vps_task.ps1
# Toda la salida queda en logs\console.log
# ============================================================
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') | Out-Null
$log = Join-Path $root 'logs\console.log'

# Usa el venv del proyecto si existe; si no, el python del sistema
$python = 'python'
foreach ($venv in @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe')) {
  $candidate = Join-Path $root $venv
  if (Test-Path $candidate) { $python = $candidate; break }
}

while ($true) {
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Add-Content $log "`n===== [$stamp] Iniciando bot ($python main.py) ====="
  & $python main.py *>> $log
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Add-Content $log "===== [$stamp] El bot termino (codigo $LASTEXITCODE). Reinicio en 10s ====="
  Start-Sleep -Seconds 10
}
