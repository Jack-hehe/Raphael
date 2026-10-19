param(
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$Python
)

$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $IsWindows) {
    throw 'Installer verification runs only on a disposable Windows GitHub Actions runner.'
}

$verificationRoot = Join-Path $env:RUNNER_TEMP ("raphael-install-" + [guid]::NewGuid())
$installDirectory = Join-Path $verificationRoot 'Installed Raphael'
$dataDirectory = Join-Path $verificationRoot 'data'
$modelsDirectory = Join-Path $verificationRoot 'models'
$logFile = Join-Path $env:LOCALAPPDATA 'app.raphael.desktop/logs/raphael.log'
if (Test-Path -LiteralPath $logFile) { throw 'Verification requires a fresh application profile.' }
New-Item -ItemType Directory -Path $verificationRoot | Out-Null

# NSIS consumes everything after its final /D= as the destination, including spaces.
$setup = Start-Process -FilePath (Resolve-Path -LiteralPath $Installer).Path `
    -ArgumentList "/S /D=$installDirectory" -PassThru -Wait -WindowStyle Hidden
if ($setup.ExitCode -ne 0) { throw "Installer failed: $($setup.ExitCode)" }

$coreExecutable = Join-Path $installDirectory 'core/raphael-core.exe'
if (-not (Test-Path -LiteralPath $coreExecutable -PathType Leaf)) { throw 'Installed core is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $installDirectory 'licenses/THIRD_PARTY_NOTICES.txt'))) {
    throw 'Installed dependency notices are missing.'
}
& $Python (Join-Path $PSScriptRoot '../../core/packaging/smoke.py') --core $coreExecutable
if ($LASTEXITCODE -ne 0) { throw 'The installed core failed its isolated RPC smoke test.' }

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
$coreProcess = $null
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    $handshake = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($application.HasExited) { throw 'The installed desktop app exited before the core handshake.' }
        if (Test-Path -LiteralPath $logFile) {
            $handshake = [bool](Select-String -LiteralPath $logFile -SimpleMatch 'core.hello: protocol 1')
            if ($handshake) { break }
        }
        Start-Sleep -Milliseconds 200
    }
    if (-not $handshake) { throw 'The installed desktop app did not complete its core handshake.' }
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($application.Id)" |
        Where-Object { $_.ExecutablePath -eq $coreExecutable })
    if ($children.Count -ne 1) { throw 'The desktop app did not launch exactly its installed core.' }
    $coreProcess = Get-Process -Id $children[0].ProcessId
    Write-Output 'Installed desktop app: bundled core launched and protocol 1 handshake passed.'
} finally {
    if (-not $application.HasExited) { $application.Kill(); $application.WaitForExit(10000) | Out-Null }
}
if ($null -ne $coreProcess -and -not $coreProcess.WaitForExit(10000)) {
    throw 'The bundled core outlived the desktop app.'
}
Write-Output 'Installer layout, relocated core, desktop startup and child-process cleanup passed.'
