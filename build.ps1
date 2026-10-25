param([switch]$Portable)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Keep unrelated installed tools' DLLs out of dependency discovery.
$taskPythonBase = (& .\.venv\Scripts\python.exe -c 'import sys; print(sys.base_prefix)').Trim()
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$taskPythonBase;$taskPythonBase\DLLs;$(Join-Path $PSScriptRoot '.venv\Scripts')"
$taskFormat = if ($Portable) { '--onedir' } else { '--onefile' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean $taskFormat --windowed --name 声准 --icon app.ico --version-file version_info.txt --distpath release app.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
