$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
$ReleaseDir = if ($env:OMR_VELOPACK_OUTPUT_DIR) {
  [System.IO.Path]::GetFullPath($env:OMR_VELOPACK_OUTPUT_DIR)
} else {
  Join-Path $Workspace "dist\velopack"
}
$SetupPath = Join-Path $ReleaseDir "OMRaut-win-Setup.exe"
if (-not (Test-Path -LiteralPath $SetupPath -PathType Leaf)) {
  throw "Velopack setup executable not found: $SetupPath"
}

$TestTempBase = if ($env:OMR_TEST_TEMP_ROOT) {
  [System.IO.Path]::GetFullPath($env:OMR_TEST_TEMP_ROOT)
} else {
  [System.IO.Path]::GetTempPath()
}
New-Item -ItemType Directory -Path $TestTempBase -Force | Out-Null
$TestRoot = Join-Path $TestTempBase "OMRaut-update-smoke-$([guid]::NewGuid().ToString('N'))"
$TestReleaseDir = Join-Path $TestRoot "feed"
$InstallDir = Join-Path $TestRoot "installed"
New-Item -ItemType Directory -Path $TestReleaseDir -Force | Out-Null
Copy-Item -Path (Join-Path $ReleaseDir "*") -Destination $TestReleaseDir -Recurse -Force

$TestSetupPath = Join-Path $TestReleaseDir "OMRaut-win-Setup.exe"
$SetupProcess = Start-Process -FilePath $TestSetupPath -ArgumentList @("--silent", "--installto", $InstallDir) -Wait -PassThru -WindowStyle Hidden
if ($SetupProcess.ExitCode -ne 0) { throw "Velopack setup failed with exit code $($SetupProcess.ExitCode)" }

$InstalledApp = Join-Path $InstallDir "OMRaut.exe"
foreach ($InstalledArtifact in @($InstalledApp, (Join-Path $InstallDir "Update.exe"),
                                  (Join-Path $InstallDir "current\OMRaut.exe"))) {
  if (-not (Test-Path -LiteralPath $InstalledArtifact -PathType Leaf)) {
    throw "Velopack did not install required application file: $InstalledArtifact"
  }
}

$env:QT_QPA_PLATFORM = "offscreen"
$InstalledPayload = Join-Path $InstallDir "current\OMRaut.exe"
$SmokeProcess = Start-Process -FilePath $InstalledPayload -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
if ($SmokeProcess.ExitCode -ne 0) { throw "Installed application smoke test failed with exit code $($SmokeProcess.ExitCode)" }

$ReleaseManifestPath = Join-Path $TestReleaseDir "releases.win.json"
$CurrentRelease = (Get-Content -LiteralPath $ReleaseManifestPath -Raw | ConvertFrom-Json).Assets |
  Where-Object { $_.PackageId -eq "OMRaut" -and $_.Type -eq "Full" } |
  Sort-Object { [version]$_.Version } -Descending |
  Select-Object -First 1
if (-not $CurrentRelease -or $CurrentRelease.Version -notmatch '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$') {
  throw "Could not determine the installed package version for the local update test."
}
$UpdateVersion = "$($Matches.major).$($Matches.minor).$([int]$Matches.patch + 1)"
$AppDir = Join-Path $Workspace "dist\OMRaut"
$Vpk = Get-Command vpk -ErrorAction Stop
& $Vpk.Source pack --packId OMRaut --packVersion $UpdateVersion --packDir $AppDir --mainExe OMRaut.exe --packTitle "OMRaut" --icon (Join-Path $Workspace "installer\OMRaut.ico") --packAuthors "OMRaut" --runtime win-x64 --outputDir $TestReleaseDir
if ($LASTEXITCODE -ne 0) { throw "Could not build local OTA update $UpdateVersion (exit code $LASTEXITCODE)" }

$Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$Listener.Start()
$Port = $Listener.LocalEndpoint.Port
$Listener.Stop()
$FeedUrl = "http://127.0.0.1:$Port"
$Python = (Get-Command python -ErrorAction Stop).Source
$ServerArgs = "-m http.server $Port --bind 127.0.0.1 --directory `"$TestReleaseDir`""
$Server = Start-Process -FilePath $Python -ArgumentList $ServerArgs -WindowStyle Hidden -PassThru
$PreviousUpdateSource = $env:OMR_UPDATE_SOURCE_URL
try {
  $FeedReady = $false
  for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    if ($Server.HasExited) { throw "Local update feed server exited with code $($Server.ExitCode)" }
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "$FeedUrl/releases.win.json" -TimeoutSec 2 | Out-Null
      $FeedReady = $true
      break
    } catch { Start-Sleep -Seconds 1 }
  }
  if (-not $FeedReady) { throw "Local update feed did not respond at $FeedUrl" }

  $env:OMR_UPDATE_SOURCE_URL = $FeedUrl
  $MarkerPath = Join-Path $TestRoot "update-completed.txt"
  $UpdateArguments = @("--update-smoke-test", $UpdateVersion, "`"$MarkerPath`"")
  $UpdateProcess = Start-Process -FilePath $InstalledApp -ArgumentList $UpdateArguments -WindowStyle Hidden -PassThru
  $Deadline = [DateTime]::UtcNow.AddMinutes(4)
  while (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf) -and [DateTime]::UtcNow -lt $Deadline) {
    if ($UpdateProcess.HasExited -and $UpdateProcess.ExitCode -ne 0) {
      throw "Installed updater process failed with exit code $($UpdateProcess.ExitCode)"
    }
    Start-Sleep -Seconds 1
  }
  if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) {
    throw "The installed app did not restart into version $UpdateVersion within four minutes."
  }
  $AppliedVersion = Get-Content -LiteralPath $MarkerPath -Raw
  if ($AppliedVersion -ne $UpdateVersion) { throw "Updated app reported $AppliedVersion; expected $UpdateVersion" }
} finally {
  $env:OMR_UPDATE_SOURCE_URL = $PreviousUpdateSource
  if (-not $Server.HasExited) { Stop-Process -Id $Server.Id -Force }
}

Write-Output "Installed the Velopack package, passed the OMR smoke test, and applied OTA update $UpdateVersion."
Remove-Item -LiteralPath $TestRoot -Recurse -Force
