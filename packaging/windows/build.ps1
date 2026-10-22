param(
    [switch]$SkipDeps,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Dist = Join-Path $PSScriptRoot "dist"
$Build = Join-Path $PSScriptRoot "build"

Write-Host "Building Center Stage $Version for Windows"

if (-not $SkipDeps) {
    python -m pip install --upgrade pip
    python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    python -m pip install -r (Join-Path $PSScriptRoot "build-requirements.txt")
    python -m pip install --no-deps "mediapipe==0.10.35"
}

$Locale = Join-Path $Root "build\locale\vi\LC_MESSAGES"
New-Item -ItemType Directory -Force -Path $Locale | Out-Null
python (Join-Path $Root "packaging\compile_translations.py") `
    (Join-Path $Root "po\vi.po") `
    (Join-Path $Locale "center-stage.mo")

$VersionParts = $Version.Split(".")
$VersionTuple = "$($VersionParts[0]), $($VersionParts[1]), $($VersionParts[2]), 0"
$VersionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($VersionTuple),
    prodvers=($VersionTuple),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Center Stage contributors'),
        StringStruct('FileDescription', 'Center Stage virtual camera'),
        StringStruct('FileVersion', '$Version'),
        StringStruct('InternalName', 'CenterStage'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', 'CenterStage.exe'),
        StringStruct('ProductName', 'Center Stage'),
        StringStruct('ProductVersion', '$Version')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
Set-Content -Path (Join-Path $PSScriptRoot "version_info.txt") -Value $VersionInfo -Encoding UTF8

python -m PyInstaller --noconfirm --clean `
    --distpath $Dist `
    --workpath $Build `
    (Join-Path $PSScriptRoot "center-stage.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Exe = Join-Path $Dist "CenterStage\CenterStage.exe"
if (-not (Test-Path $Exe)) { throw "PyInstaller output is missing: $Exe" }
& $Exe --selftest
if ($LASTEXITCODE -ne 0) { throw "Windows selftest failed" }

$Zip = Join-Path $Dist "CenterStage-$Version-win64.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path (Join-Path $Dist "CenterStage\*") -DestinationPath $Zip

if (-not $SkipInstaller) {
    $Iscc = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) {
        throw "Inno Setup 6 is not installed. Install it with: choco install innosetup -y"
    }
    & $Iscc "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    $Installer = Join-Path $Dist "CenterStage-Setup-$Version.exe"
    Get-FileHash $Installer -Algorithm SHA256 |
        ForEach-Object { "$($_.Hash.ToLower())  $(Split-Path $_.Path -Leaf)" } |
        Set-Content (Join-Path $Dist "SHA256SUMS-windows.txt")
    Write-Host "Installer: $Installer"
}

Get-FileHash $Zip -Algorithm SHA256 |
    ForEach-Object { "$($_.Hash.ToLower())  $(Split-Path $_.Path -Leaf)" } |
    Add-Content (Join-Path $Dist "SHA256SUMS-windows.txt")
Write-Host "Portable: $Zip"
