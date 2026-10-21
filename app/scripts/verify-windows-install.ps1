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
$localDirectory = Join-Path $env:LOCALAPPDATA 'Raphael'
$logFile = Join-Path $localDirectory 'logs/raphael.log'
$webviewDirectory = Join-Path $localDirectory 'WebView2'
$legacyDirectories = @((Join-Path $env:LOCALAPPDATA 'app.raphael.desktop'), (Join-Path $env:APPDATA 'app.raphael.desktop'))
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

# How the checks launch the installed desktop app: hidden, with isolated data.
function New-AppLaunch {
    param([string]$Executable, [string[]]$Arguments = @())
    $launch = [System.Diagnostics.ProcessStartInfo]::new()
    $launch.FileName = $Executable
    foreach ($argument in $Arguments) { $launch.ArgumentList.Add($argument) }
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
    return $launch
}

# Starts the installed desktop app with isolated data, waits for its own core handshake and checks
# that the one child it launched is the installed core. The caller stops it with Stop-InstalledApp.
function Start-InstalledApp {
    param([string]$Description)
    $applications = @(Get-ChildItem -LiteralPath $installDirectory -File -Filter '*.exe' |
        Where-Object { $_.Name -notmatch 'uninstall' })
    if ($applications.Count -ne 1) { throw 'Expected exactly one installed desktop executable.' }

    $application = [System.Diagnostics.Process]::Start((New-AppLaunch -Executable $applications[0].FullName))
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
        # Every window keeps its webview data in Raphael's own local folder, and nothing is written
        # under the app identifier's folders.
        if (-not (Test-Path -LiteralPath $webviewDirectory -PathType Container)) { throw "The webview data is not in $webviewDirectory ($Description)." }
        foreach ($legacy in $legacyDirectories) {
            if (Test-Path -LiteralPath $legacy) { throw "The desktop app wrote to $legacy ($Description)." }
        }
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

# Launches the installed app again while $Instance runs, with $Arguments (none, or --autostart as
# Windows does at sign-in). The launch must hand over to the running app and exit before it starts
# anything of its own: the running app hears of it, the log shows no second start-up, and there is
# still one core.
function Assert-SecondLaunchHandsOver {
    param($Instance, [string[]]$Arguments, [string]$Description)
    $count = { param([string]$Text) @(Select-String -LiteralPath $logFile -SimpleMatch $Text).Count }
    $startups = & $count 'starting the core'
    $handovers = & $count 'another launch handed over'
    $launch = New-AppLaunch -Executable $Instance.Application.Path -Arguments $Arguments
    $second = [System.Diagnostics.Process]::Start($launch)
    $null = $second.Handle  # keeps the exit code readable once the process is gone
    if (-not $second.WaitForExit(30000)) {
        $second.Kill($true)
        throw "A second launch ($Description) did not exit within 30 seconds."
    }
    if ($second.ExitCode -ne 0) { throw "A second launch ($Description) exited with $($second.ExitCode)." }
    if ($Instance.Application.HasExited) { throw "A second launch ($Description) ended the running app." }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((& $count 'another launch handed over') -le $handovers -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    if ((& $count 'another launch handed over') -le $handovers) {
        throw "The running app did not hear of a second launch ($Description)."
    }
    if ((& $count 'starting the core') -ne $startups) {
        throw "A second launch ($Description) started the app again instead of handing over."
    }
    $cores = @(Get-CimInstance Win32_Process -Filter "Name = 'raphael-core.exe'" |
        Where-Object { $_.ExecutablePath -eq $coreExecutable })
    if ($cores.Count -ne 1) { throw "After a second launch ($Description), $($cores.Count) installed cores run." }
    Write-Host "Second launch ($Description): handed over to the running app and exited."
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
    Assert-SecondLaunchHandsOver -Instance $second -Arguments @() -Description 'plain launch'
    Assert-SecondLaunchHandsOver -Instance $second -Arguments @('--autostart') -Description 'start with Windows'
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
Write-Output 'Installer layout, relocated core, desktop startup, second-launch handover, upgrade over the running app, silent uninstall and child-process cleanup passed.'
