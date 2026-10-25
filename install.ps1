$ErrorActionPreference = 'Stop'
$taskSource = $PSScriptRoot
$taskInstall = Join-Path $env:LOCALAPPDATA 'Programs\VocalPitch'
$taskExe = Join-Path $taskInstall '声准.exe'
$taskRunning = @(Get-Process -Name '声准' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $taskExe })
foreach ($taskProcess in $taskRunning) {
    if ($taskProcess.MainWindowHandle -ne [IntPtr]::Zero) {
        $null = $taskProcess.CloseMainWindow()
    }
}
foreach ($taskProcess in $taskRunning) {
    if (-not $taskProcess.WaitForExit(6000)) {
        throw '声准仍在关闭，请稍后再安装。'
    }
}
New-Item -ItemType Directory -Path $taskInstall -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $taskSource 'release\声准.exe') -Destination $taskExe -Force
Copy-Item -LiteralPath (Join-Path $taskSource '使用说明.md') -Destination $taskInstall -Force
$taskShell = New-Object -ComObject WScript.Shell
$taskDesktop = [Environment]::GetFolderPath('Desktop')
$taskMenu = Join-Path ([Environment]::GetFolderPath('Programs')) '声准'
New-Item -ItemType Directory -Path $taskMenu -Force | Out-Null
foreach ($taskLink in @((Join-Path $taskDesktop '声准.lnk'), (Join-Path $taskMenu '声准.lnk'))) {
    $taskShortcut = $taskShell.CreateShortcut($taskLink)
    $taskShortcut.TargetPath = $taskExe
    $taskShortcut.WorkingDirectory = $taskInstall
    $taskShortcut.IconLocation = "$taskExe,0"
    $taskShortcut.Description = '声准 · 实时声乐音准测量'
    $taskShortcut.Save()
}
Write-Output "Installed: $taskExe"
Write-Output "Desktop: $(Join-Path $taskDesktop '声准.lnk')"
