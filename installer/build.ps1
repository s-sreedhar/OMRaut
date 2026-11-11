$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
Set-Location $Workspace

$PythonCommand = if ($env:OMR_PYTHON) { $env:OMR_PYTHON } else { (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $PythonCommand -PathType Leaf)) { throw "OMR_PYTHON must point to a Python executable: $PythonCommand" }
$PythonVersion = & $PythonCommand -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($PythonVersion -notin @("3.11", "3.12", "3.13")) { throw "Build with Python 3.11, 3.12, or 3.13 (found $PythonVersion)." }
$Vpk = Get-Command vpk -ErrorAction SilentlyContinue
if (-not $Vpk) { throw "Velopack CLI vpk 1.2.161 is required. Install it with: dotnet tool install --global vpk --version 1.2.161" }
$VpkHelp = (& $Vpk.Source --help) -join "`n"
if ($LASTEXITCODE -ne 0 -or $VpkHelp -notmatch "Velopack CLI 1\.2\.161") {
  throw "This app requires Velopack CLI vpk 1.2.161 to match the Python package. Run: dotnet tool update --global vpk --version 1.2.161"
}
& $PythonCommand -m pip install --no-build-isolation -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "Installing build dependencies failed with exit code $LASTEXITCODE" }

$AppVersion = & $PythonCommand -c "from omr_exam import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0 -or $AppVersion -notmatch '^\d+\.\d+\.\d+$') { throw "Could not read a valid MAJOR.MINOR.PATCH application version." }
$UpdateConfig = Join-Path $Workspace "config\update.json"
$OriginalUpdateConfig = Get-Content -LiteralPath $UpdateConfig -Raw
try {
  $BuildUpdateConfig = @{ source_url = $env:OMR_UPDATE_SOURCE_URL } | ConvertTo-Json
  $Utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($UpdateConfig, $BuildUpdateConfig, $Utf8NoBom)
  Get-Process -Name "OMRaut" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 300
  $Cv2Lock = Join-Path $Workspace "dist\OMRaut\_internal\cv2\cv2.pyd"
  if (Test-Path -LiteralPath $Cv2Lock) {
    try {
      $Stream = [System.IO.File]::OpenWrite($Cv2Lock)
      $Stream.Dispose()
    } catch {
      throw "Cannot rebuild: OMRaut is currently running and locking '$Cv2Lock'. Please close the running OMRaut window or terminate it from Task Manager, then run build.bat again."
    }
  }
  & $PythonCommand -m PyInstaller --noconfirm --clean omr_exam.spec
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
  $PackagedApp = Join-Path $Workspace "dist\OMRaut\OMRaut.exe"
  if (-not (Test-Path -LiteralPath $PackagedApp)) { throw "PyInstaller did not produce $PackagedApp" }
  & $PackagedApp --smoke-test
  if ($LASTEXITCODE -ne 0) { throw "Packaged application smoke test failed with exit code $LASTEXITCODE" }
} finally {
  $Utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($UpdateConfig, $OriginalUpdateConfig, $Utf8NoBom)
}

$OutputDir = if ($env:OMR_VELOPACK_OUTPUT_DIR) {
  [System.IO.Path]::GetFullPath($env:OMR_VELOPACK_OUTPUT_DIR)
} else {
  Join-Path $Workspace "dist\velopack"
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$ExistingFeedPath = Join-Path $OutputDir "releases.win.json"
if (Test-Path -LiteralPath $ExistingFeedPath -PathType Leaf) {
  $ExistingEntries = (Get-Content -LiteralPath $ExistingFeedPath -Raw | ConvertFrom-Json).Assets
  $ExistingPackageIds = @($ExistingEntries | ForEach-Object PackageId | Sort-Object -Unique)
  if ($ExistingPackageIds.Count -gt 0 -and ($ExistingPackageIds.Count -ne 1 -or $ExistingPackageIds[0] -ne "OMRaut")) {
    throw "Output directory contains a different Velopack package ($($ExistingPackageIds -join ', ')); choose a clean output directory."
  }
  if ($ExistingEntries | Where-Object { $_.PackageId -eq "OMRaut" -and $_.Version -eq $AppVersion }) {
    throw "Version $AppVersion already exists in '$OutputDir'. Increment the app version and use a new release tag."
  }
}
$TempParent = if ($env:OMR_VELOPACK_TEMP_DIR) {
  [System.IO.Path]::GetFullPath($env:OMR_VELOPACK_TEMP_DIR)
} else {
  Join-Path $Workspace "tmp"
}
New-Item -ItemType Directory -Path $TempParent -Force | Out-Null
$VpkTempPath = Join-Path $TempParent "velopack-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $VpkTempPath | Out-Null
$PreviousTemp = $env:TEMP
$PreviousTmp = $env:TMP
try {
  # Velopack expands full packages in its temp directory; keep this large copy off a small OS drive.
  $env:TEMP = $VpkTempPath
  $env:TMP = $VpkTempPath
  & $Vpk.Source pack --packId OMRaut --packVersion $AppVersion --packDir (Join-Path $Workspace "dist\OMRaut") --mainExe OMRaut.exe --packTitle "OMRaut" --icon (Join-Path $Workspace "installer\OMRaut.ico") --packAuthors "OMRaut" --runtime win-x64 --outputDir $OutputDir
  if ($LASTEXITCODE -ne 0) { throw "Velopack packaging failed with exit code $LASTEXITCODE" }
  $SetupPath = Join-Path $OutputDir "OMRaut-win-Setup.exe"
  $PortablePath = Join-Path $OutputDir "OMRaut-win-Portable.zip"
  $FeedPath = Join-Path $OutputDir "releases.win.json"
  foreach ($RequiredArtifact in @($SetupPath, $PortablePath, $FeedPath)) {
    if (-not (Test-Path -LiteralPath $RequiredArtifact -PathType Leaf)) {
      throw "Velopack did not create required release artifact: $RequiredArtifact"
    }
  }
  $ReleaseManifest = Get-Content -LiteralPath $FeedPath -Raw | ConvertFrom-Json
  $FullPackage = $ReleaseManifest.Assets | Where-Object { $_.PackageId -eq "OMRaut" -and $_.Version -eq $AppVersion -and $_.Type -eq "Full" }
  if (-not $FullPackage -or -not (Test-Path -LiteralPath (Join-Path $OutputDir $FullPackage.FileName) -PathType Leaf)) {
    throw "Velopack release feed does not reference the full OMRaut package for version $AppVersion"
  }
} finally {
  $env:TEMP = $PreviousTemp
  $env:TMP = $PreviousTmp
  if ([System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($VpkTempPath)).TrimEnd('\') -eq $TempParent.TrimEnd('\')) {
    Remove-Item -LiteralPath $VpkTempPath -Recurse -Force -ErrorAction SilentlyContinue
  }
}
Write-Output "Velopack setup, portable package, and update feed created under $OutputDir"
