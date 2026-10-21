param(
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$Python
)

$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or -not $IsWindows) {
    throw 'Installer verification runs only on a disposable Windows GitHub Actions runner.'
}

$Installer = (Resolve-Path -LiteralPath $Installer).Path
$verificationRoot = Join-Path $env:RUNNER_TEMP ("raphael-install-" + [guid]::NewGuid())
$installDirectory = Join-Path $verificationRoot 'Installed Raphael'
$dataDirectory = Join-Path $verificationRoot 'data'
$modelsDirectory = Join-Path $verificationRoot 'models'
$logFile = Join-Path $env:LOCALAPPDATA 'app.raphael.desktop/logs/raphael.log'
# Where the core keeps its data when RAPHAEL_DATA_DIR is not set. The checks put one marker file here to
# show that an upgrade and a silent uninstall keep user data, and remove nothing but that marker (and the
# folder, if the checks created it and it is empty again).
$userDataDirectory = Join-Path $env:APPDATA 'Raphael'
$userDataMarker = Join-Path $userDataDirectory 'installer-verification.txt'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Raphael'
if (Test-Path -LiteralPath $logFile) { throw 'Verification requires a fresh application profile.' }
if (Test-Path -LiteralPath $uninstallKey) { throw 'Verification requires a computer without an installed Raphael.' }
New-Item -ItemType Directory -Path $verificationRoot | Out-Null

function Invoke-Setup {
    param([string]$Executable, [string]$Arguments, [string]$Description)
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    $null = $process.Handle  # keeps the exit code readable once the process is gone
    if (-not $process.WaitForExit(600000)) {
        $process.Kill($true)
        throw "$Description did not finish within 10 minutes."
    }
    if ($process.ExitCode -ne 0) { throw "$Description failed: $($process.ExitCode)" }
}

# NSIS consumes everything after its final /D= as the destination, including spaces.
function Install-Raphael {
    param([string]$Description)
    Invoke-Setup -Executable $Installer -Arguments "/S /D=$installDirectory" -Description $Description
}

function Assert-InstalledFiles {
    param([string]$Description)
    if (-not (Test-Path -LiteralPath $coreExecutable -PathType Leaf)) { throw "Installed core is missing ($Description)." }
    if (-not (Test-Path -LiteralPath (Join-Path $installDirectory 'licenses/THIRD_PARTY_NOTICES.txt'))) {
        throw "Installed dependency notices are missing ($Description)."
    }
}

# Starts the installed desktop app with isolated data, waits for its own core handshake and checks
# that the one child it launched is the installed core. The caller stops it with Stop-InstalledApp.
function Start-InstalledApp {
    param([string]$Description)
    $applications = @(Get-ChildItem -LiteralPath $installDirectory -File -Filter '*.exe' |
        Where-Object { $_.Name -notmatch 'uninstall' })
    if ($applications.Count -ne 1) { throw 'Expected exactly one installed desktop executable.' }

    $launch = [System.Diagnostics.ProcessStartInfo]::new()
    $launch.FileName = $applications[0].FullName
    $launch.WorkingDirectory = $verificationRoot
    $launch.UseShellExecute = $false
    $launch.CreateNoWindow = $true
    $launch.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $launch.Environment['RAPHAEL_DATA_DIR'] = $dataDirectory
    $launch.Environment['RAPHAEL_MODELS_DIR'] = $modelsDirectory
    $launch.Environment['PYTHON_KEYRING_BACKEND'] = 'keyring.backends.fail.Keyring'
    # Poison development overrides: an installed release must still use its own core.
    $launch.Environment['RAPHAEL_CORE_PYTHON'] = Join-Path $verificationRoot 'missing-python.exe'
    $launch.Environment['PYTHONPATH'] = Join-Path $verificationRoot 'missing-source'
    $application = [System.Diagnostics.Process]::Start($launch)
    $instance = [pscustomobject]@{ Application = $application; Core = $null }
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(90)
        $handshake = $false
        while ([DateTime]::UtcNow -lt $deadline) {
            if ($application.HasExited) { throw "The installed desktop app exited before the core handshake ($Description)." }
            if (Test-Path -LiteralPath $logFile) {
                $handshake = [bool](Select-String -LiteralPath $logFile -SimpleMatch 'core.hello: protocol 1')
                if ($handshake) { break }
            }
            Start-Sleep -Milliseconds 200
        }
        if (-not $handshake) { throw "The installed desktop app did not complete its core handshake ($Description)." }
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($application.Id)" |
            Where-Object { $_.ExecutablePath -eq $coreExecutable })
        if ($children.Count -ne 1) { throw "The desktop app did not launch exactly its installed core ($Description)." }
        $instance.Core = Get-Process -Id $children[0].ProcessId
    } catch {
        if (-not $application.HasExited) { $application.Kill($true); $application.WaitForExit(10000) | Out-Null }
        throw
    }
    Write-Host "Installed desktop app ($Description): bundled core launched and protocol 1 handshake passed."
    return $instance
}

function Stop-InstalledApp {
    param($Instance, [string]$Description)
    if (-not $Instance.Application.HasExited) { $Instance.Application.Kill(); $Instance.Application.WaitForExit(10000) | Out-Null }
    if (-not $Instance.Core.WaitForExit(10000)) { throw "The bundled core outlived the desktop app ($Description)." }
}

$coreExecutable = Join-Path $installDirectory 'core/raphael-core.exe'
$running = @()
$createdUserDataDirectory = $false
try {
    Install-Raphael -Description 'Installer'
    Assert-InstalledFiles -Description 'fresh install'
    & $Python (Join-Path $PSScriptRoot '../../core/packaging/smoke.py') --core $coreExecutable
    if ($LASTEXITCODE -ne 0) { throw 'The installed core failed its isolated RPC smoke test.' }

    $createdUserDataDirectory = -not (Test-Path -LiteralPath $userDataDirectory)
    New-Item -ItemType Directory -Force -Path $userDataDirectory | Out-Null
    Set-Content -LiteralPath $userDataMarker -Value 'created by verify-windows-install.ps1' -Encoding ascii

    $first = Start-InstalledApp -Description 'fresh install'
    $running += $first

    # Upgrade path: the same installer run again, silently, over the running app. The installer must
    # close the app (and with it the core) to replace their files, and keep the user's data.
    Install-Raphael -Description 'Upgrade over the running app'
    if (-not $first.Application.WaitForExit(30000)) { throw 'The upgrade left the old desktop app running.' }
    Stop-InstalledApp -Instance $first -Description 'upgrade'
    Assert-InstalledFiles -Description 'after the upgrade'
    if (-not (Test-Path -LiteralPath $userDataMarker)) { throw 'The upgrade removed user data.' }

    # The log is appended to: remove it so that the second handshake cannot be the first one.
    Remove-Item -LiteralPath $logFile
    $second = Start-InstalledApp -Description 'after the upgrade'
    $running += $second
    Stop-InstalledApp -Instance $second -Description 'after the upgrade'

    # Uninstall silently, as a user would from Apps & features. The NSIS uninstaller copies itself to
    # %TEMP% so that it can delete its own folder, which makes its first process return early: wait
    # for the result instead. A silent uninstall never shows the "delete app data" box, so it must
    # keep user data.
    $uninstaller = Join-Path $installDirectory 'uninstall.exe'
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw 'The installer did not write uninstall.exe.' }
    if (-not (Test-Path -LiteralPath $uninstallKey)) { throw 'The installer did not register itself for Apps & features.' }
    Invoke-Setup -Executable $uninstaller -Arguments '/S' -Description 'Silent uninstall'
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while (((Test-Path -LiteralPath $installDirectory) -or (Test-Path -LiteralPath $uninstallKey)) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (Test-Path -LiteralPath $installDirectory) {
        $left = @(Get-ChildItem -LiteralPath $installDirectory -Recurse -Force | Select-Object -First 20 -ExpandProperty FullName)
        throw "The silent uninstall left the install directory behind: $($left -join ', ')"
    }
    if (Test-Path -LiteralPath $uninstallKey) { throw 'The silent uninstall left its Apps & features entry behind.' }
    if (-not (Test-Path -LiteralPath $userDataMarker)) { throw 'A silent uninstall removed user data; only the ticked "delete app data" box may.' }
} finally {
    foreach ($instance in $running) {
        if (-not $instance.Application.HasExited) { $instance.Application.Kill($true); $instance.Application.WaitForExit(10000) | Out-Null }
    }
    # Remove only what this script created: its marker, then its folder if it made it and nothing else is in it.
    if (Test-Path -LiteralPath $userDataMarker) { Remove-Item -LiteralPath $userDataMarker -Force }
    if ($createdUserDataDirectory -and (Test-Path -LiteralPath $userDataDirectory) -and -not (Get-ChildItem -LiteralPath $userDataDirectory -Force)) {
        Remove-Item -LiteralPath $userDataDirectory
    }
}
Write-Output 'Installer layout, relocated core, desktop startup, upgrade over the running app, silent uninstall and child-process cleanup passed.'
