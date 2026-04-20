# Registriert einen wöchentlichen Task Scheduler Job, der cleanup.bat ausführt.
# Ausführung: Rechtsklick → "Mit PowerShell ausführen" (einmalig, muss nicht Admin sein — läuft als aktueller User).

$TaskName  = "AI_Agent_Cleanup"
$BatFile   = Join-Path $PSScriptRoot "..\cleanup.bat" | Resolve-Path
$TriggerAt = "04:00"

# Existierenden Task entfernen (falls vorhanden)
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BatFile`""
$Trigger   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $TriggerAt
$Settings  = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName    $TaskName `
    -Description "AI Research Agent: wöchentlich alte logs/results + fehlgeschlagene RBs aufräumen" `
    -Action      $Action `
    -Trigger     $Trigger `
    -Settings    $Settings `
    -Principal   $Principal | Out-Null

Write-Host ""
Write-Host "OK — Task registriert:" -ForegroundColor Green
Write-Host "  Name    : $TaskName"
Write-Host "  Wann    : jeden Sonntag um $TriggerAt"
Write-Host "  Befehl  : $BatFile"
Write-Host ""
Write-Host "Jetzt testen (sofort ausfuehren)   : Start-ScheduledTask -TaskName $TaskName"
Write-Host "Status pruefen                      : Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "Wieder entfernen                    : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ""
Write-Host "Log-Ausgabe landet in logs\cleanup_YYYYMMDD.log"
