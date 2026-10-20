$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$dumpFolder = Join-Path $projectRoot "crash_dumps\windows_dumps"
$registryPath = "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\python.exe"

New-Item -ItemType Directory -Path $dumpFolder -Force | Out-Null
New-Item -Path $registryPath -Force | Out-Null
New-ItemProperty -Path $registryPath -Name "DumpFolder" -PropertyType ExpandString -Value $dumpFolder -Force | Out-Null
New-ItemProperty -Path $registryPath -Name "DumpCount" -PropertyType DWord -Value 10 -Force | Out-Null
New-ItemProperty -Path $registryPath -Name "DumpType" -PropertyType DWord -Value 2 -Force | Out-Null

Write-Host "Windows full crash dumps are enabled for python.exe."
Write-Host "Dump folder: $dumpFolder"
Write-Host "DumpType: 2 (full dump), DumpCount: 10"
