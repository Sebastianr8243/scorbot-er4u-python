# Install the Python environment on the Windows robot PC. No USB I/O or motion.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot

$pythonCommand = $null
$pythonArguments = @()
$pythonVersion = $null

foreach ($candidate in @(
    @{ Name = 'py'; Arguments = @('-3') },
    @{ Name = 'python'; Arguments = @() }
)) {
    $command = Get-Command $candidate.Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $command) { continue }
    $arguments = $candidate.Arguments
    try {
        $result = & $command.Source @arguments -c 'import sys; print(sys.version_info[0], sys.version_info[1], sys.version_info[2], sep=chr(46))' 2>$null
    } catch {
        continue
    }
    if ($LASTEXITCODE -ne 0 -or $result -notmatch '^\d+\.\d+\.\d+$') { continue }
    $version = [version]$result
    if ($version -ge [version]'3.10') {
        $pythonCommand = $command.Source
        $pythonArguments = $arguments
        $pythonVersion = $version
        break
    }
}

if ($null -eq $pythonCommand) {
    Write-Host 'Python 3.10 or newer was not found.'
    Write-Host 'Install Python 3.13 for Windows from https://www.python.org/downloads/release/python-31315/'
    Write-Host 'Then reopen the VS Code terminal and run this script again.'
    exit 2
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating .venv with Python $pythonVersion ($pythonCommand)"
    & $pythonCommand @pythonArguments -m venv '.venv'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
} else {
    $environmentVersion = & $venvPython -c 'import sys; print(sys.version_info[0], sys.version_info[1], sys.version_info[2], sep=chr(46))'
    if ($LASTEXITCODE -ne 0 -or [version]$environmentVersion -lt [version]'3.10') {
        throw 'The existing .venv does not have a working Python 3.10 or newer interpreter.'
    }
    Write-Host "Reusing .venv with Python $environmentVersion"
}

Write-Host 'Installing the ScorBot package and Windows USB backend...'
& $venvPython -m pip install -e '.[windows]'
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Save the pip error output.' }

& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Installed Python packages have a dependency conflict.' }

& $venvPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Software tests failed. Do not connect to the robot yet.' }

Write-Host 'Python setup passed. This script did not access the USB controller.'
Write-Host 'Next: connect the original ER-4U controller by USB, then run:'
Write-Host 'powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_usb_check.ps1'
