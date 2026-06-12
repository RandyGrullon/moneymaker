# ============================================================
# Instala el bot en un VPS Windows (ejecutar UNA vez, mejor como Administrador):
#   1. Crea la tarea programada 'OmniTradeBot': arranca el bot al iniciar
#      sesion y lo reintenta si falla.
#   2. Abre el puerto de la UI en el firewall de Windows.
#
# IMPORTANTE: MT5 necesita sesion grafica. En el VPS, DESCONECTA el RDP
# (no cierres sesion) para que el terminal y el bot sigan corriendo.
#
#   Uso:        powershell -ExecutionPolicy Bypass -File scripts\install_vps_task.ps1
#   Desinstalar: scripts\uninstall_vps_task.ps1
# ============================================================
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$script = Join-Path $PSScriptRoot 'start_bot.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
  -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName 'OmniTradeBot' -Action $action -Trigger $trigger `
  -Settings $settings -Force `
  -Description 'Bot de trading OmniTrade AI (auto-arranque y auto-reinicio)' | Out-Null
Write-Host "[OK] Tarea 'OmniTradeBot' instalada: el bot arranca al iniciar sesion." -ForegroundColor Green

# Puerto de la UI desde el .env (para la regla de firewall)
$port = 5000
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
  $m = Select-String -Path $envFile -Pattern '^\s*UI_PORT\s*=\s*(\d+)' | Select-Object -First 1
  if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}
try {
  if (-not (Get-NetFirewallRule -DisplayName 'OmniTrade UI' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'OmniTrade UI' -Direction Inbound -Protocol TCP `
      -LocalPort $port -Action Allow | Out-Null
  }
  Write-Host "[OK] Firewall: puerto $port abierto (regla 'OmniTrade UI')." -ForegroundColor Green
} catch {
  Write-Host "[AVISO] No se pudo abrir el puerto $port. Ejecuta como Administrador:" -ForegroundColor Yellow
  Write-Host "  New-NetFirewallRule -DisplayName 'OmniTrade UI' -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow"
}

Write-Host ""
Write-Host "Para arrancarlo AHORA sin esperar al proximo inicio de sesion:"
Write-Host "  Start-ScheduledTask -TaskName OmniTradeBot"
Write-Host "Logs: $root\logs\console.log y $root\logs\bot.log"
