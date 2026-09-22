# Read-only Windows device listing and Python USB enumeration. No robot commands.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot

if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
    try {
        $devices = @(Get-PnpDevice -PresentOnly -ErrorAction Stop |
            Where-Object { $_.InstanceId -match 'VID_09F1&PID_0007' })
        if ($devices.Count -eq 0) {
            Write-Host 'Windows does not currently list USB VID_09F1&PID_0007.'
            Write-Host 'Check controller power, USB cable, port, and Device Manager hardware IDs.'
        } else {
            Write-Host 'Windows lists the expected USB controller:'
            $devices | Select-Object FriendlyName, Status, InstanceId | Format-List
        }
    } catch {
        Write-Host "Windows device listing failed: $($_.Exception.Message)"
        Write-Host 'Check the hardware IDs manually in Device Manager.'
    }
} else {
    Write-Host 'Get-PnpDevice is unavailable; check hardware IDs in Device Manager.'
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Python environment not found. Run scripts\setup_windows.ps1 first.'
    exit 2
}

Write-Host 'Running read-only Python USB preflight...'
& $venvPython -m scorbot.preflight
$preflightCode = $LASTEXITCODE
if ($preflightCode -ne 0) {
    Write-Host 'Python cannot use the controller yet. See docs\WINDOWS_BENCH_RUN.md before changing any driver.'
}
exit $preflightCode
