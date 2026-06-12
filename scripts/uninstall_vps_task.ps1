# Quita la tarea programada y la regla de firewall del bot.
# (Si el bot esta corriendo ahora, cierra tambien su proceso python.)
try { Stop-ScheduledTask -TaskName 'OmniTradeBot' -ErrorAction SilentlyContinue } catch {}
try {
  Unregister-ScheduledTask -TaskName 'OmniTradeBot' -Confirm:$false -ErrorAction Stop
  Write-Host "[OK] Tarea 'OmniTradeBot' eliminada." -ForegroundColor Green
} catch {
  Write-Host "[AVISO] La tarea 'OmniTradeBot' no existia." -ForegroundColor Yellow
}
try {
  Remove-NetFirewallRule -DisplayName 'OmniTrade UI' -ErrorAction Stop
  Write-Host "[OK] Regla de firewall 'OmniTrade UI' eliminada." -ForegroundColor Green
} catch {
  Write-Host "[AVISO] No habia regla de firewall (o faltan permisos de Administrador)." -ForegroundColor Yellow
}
