$names = 'OptionDashboard-1000','OptionDashboard-1200','OptionDashboard-1400'
foreach ($name in $names) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed $name"
    }
}
Write-Host 'Scheduled tasks removed. Tradier encrypted token and logs were left in place.'
