$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
$ReleaseDir = Join-Path $Workspace "dist\velopack"
$SetupPath = Join-Path $ReleaseDir "OMRExamApp-win-Setup.exe"
if (-not (Test-Path -LiteralPath $SetupPath -PathType Leaf)) {
  throw "Velopack setup executable not found: $SetupPath"
}

$TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) "OMRExamApp-update-smoke-$([guid]::NewGuid().ToString('N'))"
$TestReleaseDir = Join-Path $TestRoot "feed"
$InstallDir = Join-Path $TestRoot "installed"
New-Item -ItemType Directory -Path $TestReleaseDir -Force | Out-Null
Copy-Item -Path (Join-Path $ReleaseDir "*") -Destination $TestReleaseDir -Recurse -Force

$TestSetupPath = Join-Path $TestReleaseDir "OMRExamApp-win-Setup.exe"
& $TestSetupPath --silent --installto $InstallDir
if ($LASTEXITCODE -ne 0) { throw "Velopack setup failed with exit code $LASTEXITCODE" }

$InstalledApp = Join-Path $InstallDir "OMRExamApp.exe"
foreach ($InstalledArtifact in @($InstalledApp, (Join-Path $InstallDir "Update.exe"),
                                  (Join-Path $InstallDir "current\OMRExamApp.exe"))) {
  if (-not (Test-Path -LiteralPath $InstalledArtifact -PathType Leaf)) {
    throw "Velopack did not install required application file: $InstalledArtifact"
  }
}

$env:QT_QPA_PLATFORM = "offscreen"
& $InstalledApp --smoke-test
if ($LASTEXITCODE -ne 0) { throw "Installed application smoke test failed with exit code $LASTEXITCODE" }

$ReleaseManifestPath = Join-Path $TestReleaseDir "releases.win.json"
$CurrentRelease = (Get-Content -LiteralPath $ReleaseManifestPath -Raw | ConvertFrom-Json).Assets |
  Where-Object { $_.PackageId -eq "OMRExamApp" -and $_.Type -eq "Full" } |
  Select-Object -First 1
if (-not $CurrentRelease -or $CurrentRelease.Version -notmatch '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$') {
  throw "Could not determine the installed package version for the local update test."
}
$UpdateVersion = "$($Matches.major).$($Matches.minor).$([int]$Matches.patch + 1)"
$AppDir = Join-Path $Workspace "dist\OMRExamApp"
$Vpk = Get-Command vpk -ErrorAction Stop
& $Vpk.Source pack --packId OMRExamApp --packVersion $UpdateVersion --packDir $AppDir --mainExe OMRExamApp.exe --packTitle "OMR Exam App" --packAuthors "OMR Exam App" --runtime win-x64 --outputDir $TestReleaseDir
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
