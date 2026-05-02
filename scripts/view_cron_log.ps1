# View last 50 lines of cron log
$log = "C:\Users\Fahad\OneDrive\Desktop\Claude Projects\Predictions Market\logs\cron.log"
if (Test-Path $log) {
    Get-Content $log -Tail 50
} else {
    Write-Host "No cron.log yet — task hasn't run."
}
