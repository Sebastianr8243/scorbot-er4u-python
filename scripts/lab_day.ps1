# Lab-day runner: the steps of lab/LAB_DAY_CARD.md (in git history) (one arm), one at a time, in order.
# It holds your labels and flags, picks a free log name for each step, and stops at the first failure.
# It adds no checks of its own: the scripts it starts do their own safety prompts and refusals.
# Before every step that connects to the arm it waits for you to type GO. Ctrl-C is not an emergency
# stop; the physical stop is. Run from the repo root, with SCORBASE closed.
#
#   .\scripts\lab_day.ps1 -DryRun                  # print every command, run nothing
#   .\scripts\lab_day.ps1 -Simulate                # whole flow against the simulator (no arm, no USB)
#   .\scripts\lab_day.ps1 -Arm ARM-1 -Operator SR  # the real thing
#   .\scripts\lab_day.ps1 -From 4                  # restart at step 4 (after a stop you resolved)
#   .\scripts\lab_day.ps1 -InchHome                # add --inch-home to the homing steps (legacy home failed)
param(
    [string]$Arm = 'ARM-1', [string]$Controller = 'CTRL-1', [string]$Operator = 'SR',
    [string]$PoseNote = 'homing start pose', [string]$Logs = 'logs',
    [int]$From = 1, [int]$Only = 0,
    [switch]$InchHome, [switch]$Simulate, [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$py = '.\.venv\Scripts\python.exe'
if (-not $DryRun -and -not (Test-Path $py)) { throw "No $py here. Run scripts\setup_windows.ps1 first." }
$id = @('--robot-id', $Arm, '--arm-label', $Arm, '--controller-label', $Controller,
        '--driver', $(if ($Simulate) { 'none (simulated)' } else { 'usb' }), '--operator', $Operator)
$home_ = if ($InchHome) { @('--inch-home') } else { @() }
$sim = if ($Simulate) { @('--simulate') } else { @() }
$motion = '--acknowledge-supervised-motion'
$leds = 'LED prompts: look at the controller panel. MOTORS lit? y / n / u(nsure). POWER g(reen) / o(range) / f(lashing) / u.'
$homing = 'Typed words: HOME, then describe home and type HOME_OK, then'

function Next-Log($name) {
    for ($n = 1; ; $n++) {
        $path = Join-Path $Logs ('{0}-{1:00}.jsonl' -f $name, $n)
        if (-not (Test-Path $path)) { return $path }
    }
}

# Each step: title, connects to the arm (GO gate), log name, a builder for the command, what to look at.
$steps = @(
    @{ t = 'USB check (reads only)'; arm = $false; log = $null; skipSim = $true
       cmd = { @('powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '.\scripts\windows_usb_check.ps1') }
       look = 'Wanted: PASS USB 09F1:0007. Anything else: stop here.' },
    @{ t = 'Idle recording (connects, no commanded motion)'; arm = $true; log = 'idle'
       cmd = { param($o) (@($py, 'examples\record_raw_state.py', '--output', $o) + $id + @('--pose-note', $PoseNote, '--seconds', '10', '--acknowledge-connect-handshake') + $sim) }
       look = "$leds Counts should sit still." },
    @{ t = 'Jog base +1 degree'; arm = $true; log = 'base'
       cmd = { param($o) (@($py, 'examples\bench_joint.py', '--output', $o) + $id + @('--start-pose-note', $PoseNote, '--joint', 'base', '--delta', '1', $motion) + $home_ + $sim) }
       look = "$homing MOVE. Note which way it turned and read the phone level before and after." },
    @{ t = 'Jog shoulder +1 degree'; arm = $true; log = 'shoulder'
       cmd = { param($o) (@($py, 'examples\bench_joint.py', '--output', $o) + $id + @('--start-pose-note', $PoseNote, '--joint', 'shoulder', '--delta', '1', $motion) + $home_ + $sim) }
       look = "$homing MOVE. The shoulder has under 4 degrees of travel above home: a refusal here is normal. Hold the arm's weight in mind." },
    @{ t = 'Jog elbow +1 degree'; arm = $true; log = 'elbow'
       cmd = { param($o) (@($py, 'examples\bench_joint.py', '--output', $o) + $id + @('--start-pose-note', $PoseNote, '--joint', 'elbow', '--delta', '1', $motion) + $home_ + $sim) }
       look = "$homing MOVE. Does the wrist move too? Say so at the prompt." },
    @{ t = 'Stream trial: base 1 degree out and back'; arm = $true; log = 'stream'
       cmd = { param($o) (@($py, 'examples\bench_stream.py', '--output', $o) + $id + @('--start-pose-note', $PoseNote, '--motor', 'base', '--delta', '1', $motion) + $home_ + $sim) }
       look = "$homing STREAM. It should go out 1 degree and come back." },
    @{ t = 'Policy run: base reach 1 degree, 20 steps'; arm = $true; log = 'policy'
       cmd = { param($o) (@($py, 'examples\policy_loop.py', '--output', $o) + $id + @('--start-pose-note', $PoseNote, '--policy', 'reach:base=1', $motion) + $home_ + $sim) }
       look = "$homing POLICY. Then the after-run LED prompts. Only do this step after steps 3 and 6 went well." },
    @{ t = 'Export the policy run as a dataset'; arm = $false; log = $null; skipSim = $false
       cmd = { @($py, 'scripts\export_policy_dataset.py', $script:lastPolicy, '--out', (Join-Path 'datasets' ('run-{0:00}' -f $script:datasetNo))) }
       look = 'Wanted: "reloaded and matched the source rows".' }
)

if (-not $DryRun) { New-Item -ItemType Directory -Force $Logs | Out-Null }
$script:lastPolicy = $null
$script:datasetNo = 1
while (Test-Path (Join-Path 'datasets' ('run-{0:00}' -f $script:datasetNo))) { $script:datasetNo++ }
$made = @()
$last = $steps.Count
for ($i = 1; $i -le $last; $i++) {
    if ($i -lt $From -or ($Only -gt 0 -and $i -ne $Only)) { continue }
    $s = $steps[$i - 1]
    if ($Simulate -and $s.skipSim) { Write-Host "[$i/$last] skipped in -Simulate: $($s.t)"; continue }
    $log = if ($s.log) { Next-Log $s.log } else { $null }
    if ($log -and $s.log -eq 'policy') { $script:lastPolicy = $log }
    if ($i -eq $last -and -not $script:lastPolicy) {
        $found = Get-ChildItem $Logs -Filter 'policy-*.jsonl' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
        if (-not $found) { Write-Host "[$i/$last] no policy log in $Logs; nothing to export."; continue }
        $script:lastPolicy = $found.FullName
    }
    $argv = @(& $s.cmd $log)
    Write-Host ""
    Write-Host "=== [$i/$last] $($s.t) ===" -ForegroundColor Cyan
    Write-Host ($argv -join ' ')
    Write-Host $s.look
    if ($DryRun) { continue }
    if ($s.arm -and -not $Simulate) {
        Write-Host "Hand on the PHYSICAL STOP. Nobody near the arm's path." -ForegroundColor Yellow
        if ((Read-Host 'Type GO to start this step (anything else quits)') -ne 'GO') { Write-Host 'Stopped by you. Nothing was started.'; exit 3 }
    }
    & $argv[0] $argv[1..($argv.Count - 1)]
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "STEP $i FAILED (exit $LASTEXITCODE). Do not retry it. Keep $Logs, tell the supervisor what you saw." -ForegroundColor Red
        Write-Host "To resume after you resolve it: .\scripts\lab_day.ps1 -From $($i + 1)"
        exit $LASTEXITCODE
    }
    if ($log -and (Test-Path $log)) {
        $made += $log
        & $py scripts\lab_summary.py $log
    }
}
if ($made.Count -gt 1 -and -not $DryRun) {
    Write-Host ""
    Write-Host '=== Summary of this run ===' -ForegroundColor Cyan
    & $py scripts\lab_summary.py @made
    Write-Host "Copy $Logs to a USB stick. Add --phone joint=degrees to lab_summary.py for the angles you read."
}
