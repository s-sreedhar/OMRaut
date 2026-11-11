$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
Set-Location $Workspace

$Repo = "s-sreedhar/OMRaut"
$Remote = "origin"
$Branch = "main"
$FeedDir = Join-Path $Workspace "dist\velopack-public-ota-final"
$TempRoot = Join-Path $Workspace "tmp\release-temp"
$TestRoot = Join-Path $Workspace "tmp\release-test"
$UpdateSource = "https://github.com/$Repo"
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Read-ProjectVersion {
  $ModuleText = [System.IO.File]::ReadAllText((Join-Path $Workspace "src\omr_exam\__init__.py"))
  $ProjectText = [System.IO.File]::ReadAllText((Join-Path $Workspace "pyproject.toml"))
  $ModuleMatch = [regex]::Match($ModuleText, '(?m)^__version__\s*=\s*["''](?<version>\d+\.\d+\.\d+)["'']\s*$')
  $ProjectMatch = [regex]::Match($ProjectText, '(?ms)^\[project\]\s*.*?^version\s*=\s*["''](?<version>\d+\.\d+\.\d+)["'']\s*$')
  if (-not $ModuleMatch.Success -or -not $ProjectMatch.Success) {
    throw "Could not read the app version from __init__.py and pyproject.toml."
  }
  if ($ModuleMatch.Groups['version'].Value -ne $ProjectMatch.Groups['version'].Value) {
    throw "App version differs between __init__.py and pyproject.toml. Correct the mismatch first."
  }
  return $ModuleMatch.Groups['version'].Value
}

function Test-RemoteTagExists([string]$Version) {
  $Tag = "v$Version"
  $TagOutput = & git ls-remote --tags $Remote "refs/tags/$Tag" 2>$null
  $GitResult = $LASTEXITCODE
  if ($GitResult -notin @(0, 2)) { throw "Could not check whether remote tag $Tag exists." }
  return ($GitResult -eq 0 -and [bool]$TagOutput)
}

function Test-LocalPackageReady([string]$Version) {
  $ManifestPath = Join-Path $FeedDir "releases.win.json"
  if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { return $false }
  $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
  $Entry = $Manifest.Assets | Where-Object { $_.PackageId -eq "OMRaut" -and $_.Version -eq $Version }
  if (-not $Entry) { return $false }
  foreach ($Name in @("OMRaut-win-Setup.exe", "OMRaut-win-Portable.zip", "assets.win.json", "RELEASES")) {
    if (-not (Test-Path -LiteralPath (Join-Path $FeedDir $Name) -PathType Leaf)) {
      throw "The local $Version package is already indexed, but '$Name' is missing from '$FeedDir'. Restore the artifact before publishing."
    }
  }
  $PackageFiles = @(Get-ChildItem -LiteralPath $FeedDir -File -Filter "OMRaut-$Version-*.nupkg" -ErrorAction SilentlyContinue)
  if ($PackageFiles.Count -lt 1) { throw "The local $Version feed entry has no matching .nupkg file in '$FeedDir'. Restore the artifact before publishing." }
  return $true
}

function Set-ProjectVersion([string]$Version) {
  $ModulePath = Join-Path $Workspace "src\omr_exam\__init__.py"
  $ProjectPath = Join-Path $Workspace "pyproject.toml"
  $ModuleText = [System.IO.File]::ReadAllText($ModulePath)
  $ProjectText = [System.IO.File]::ReadAllText($ProjectPath)
  $ModuleText = [regex]::Replace($ModuleText, '(?m)^(__version__\s*=\s*["''])\d+\.\d+\.\d+(["'']\s*)$', "`${1}$Version`${2}")
  $ProjectText = [regex]::Replace($ProjectText, '(?ms)(^\[project\]\s*.*?^version\s*=\s*["''])\d+\.\d+\.\d+(["'']\s*$)', "`${1}$Version`${2}")
  [System.IO.File]::WriteAllText($ModulePath, $ModuleText, $Utf8NoBom)
  [System.IO.File]::WriteAllText($ProjectPath, $ProjectText, $Utf8NoBom)
}

Write-Output "Checking GitHub CLI login and repository visibility..."
& gh auth status
if ($LASTEXITCODE -ne 0) { throw "Sign in to GitHub CLI first with: gh auth login" }
$RepoInfo = & gh repo view $Repo --json visibility,defaultBranchRef | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Could not read GitHub repository $Repo." }
if ($RepoInfo.visibility -ne "PUBLIC") { throw "$Repo is $($RepoInfo.visibility). Make it public before publishing downloads for everyone." }
if ($RepoInfo.defaultBranchRef.name -ne $Branch) { throw "Expected default branch '$Branch', found '$($RepoInfo.defaultBranchRef.name)'." }
$RemoteUrl = (& git remote get-url $Remote).Trim()
if ($LASTEXITCODE -ne 0 -or $RemoteUrl -notmatch '(?i)(github\.com[:/]s-sreedhar/OMRaut)(?:\.git)?$') {
  throw "Git remote '$Remote' must point to https://github.com/$Repo (current: $RemoteUrl)."
}
$BuildDrive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($Workspace))
if ($BuildDrive.AvailableFreeSpace -lt 4GB) {
  throw "The build drive has less than 4 GB free. Free space before packaging; all temporary build files will be placed on $($BuildDrive.Name)."
}

& git fetch $Remote
if ($LASTEXITCODE -ne 0) { throw "Could not fetch from $Remote." }
$CurrentBranch = (& git branch --show-current).Trim()
if ($CurrentBranch -ne $Branch) { throw "Switch to '$Branch' before publishing (current branch: '$CurrentBranch')." }
$Divergence = (& git rev-list --left-right --count "$Remote/$Branch...HEAD").Trim() -split '\s+'
if ($Divergence.Count -ne 2 -or [int]$Divergence[0] -gt 0) {
  throw "Local '$Branch' is behind or cannot be compared with '$Remote/$Branch'. Pull/rebase before publishing."
}

$Version = Read-ProjectVersion
while (Test-RemoteTagExists $Version) {
  $Parts = $Version.Split('.') | ForEach-Object { [int]$_ }
  $Version = "$($Parts[0]).$($Parts[1]).$($Parts[2] + 1)"
}
$Tag = "v$Version"
Set-ProjectVersion $Version
$ReusePackage = Test-LocalPackageReady $Version
Write-Output "Preparing OMRaut $Version ($Tag)."

$Python = if ($env:OMR_PYTHON) { $env:OMR_PYTHON } else { Join-Path $Workspace ".venv-build\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
  $PythonCommand = Get-Command python -ErrorAction Stop
  $Python = $PythonCommand.Source
}
$env:OMR_PYTHON = $Python
$env:OMR_UPDATE_SOURCE_URL = $UpdateSource
$env:OMR_VELOPACK_OUTPUT_DIR = $FeedDir
$env:OMR_TEST_TEMP_ROOT = $TestRoot
New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
$PreviousTemp = $env:TEMP
$PreviousTmp = $env:TMP
$env:TEMP = $TempRoot
$env:TMP = $TempRoot

try {
  if ($ReusePackage) {
    Write-Output "Reusing the complete local $Version package and feed; rerunning the installed-package check."
  } else {
    Write-Output "Building installer and OTA feed. Temporary build files are on the project drive."
    & (Join-Path $PSScriptRoot "build.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed with exit code $LASTEXITCODE." }
  }

  Write-Output "Installing the package in a temporary directory and checking its update path..."
  & (Join-Path $PSScriptRoot "test-installed-package.ps1")
  if ($LASTEXITCODE -ne 0) { throw "Installed-package update check failed with exit code $LASTEXITCODE." }
} finally {
  $env:TEMP = $PreviousTemp
  $env:TMP = $PreviousTmp
}

$RequiredFiles = @("OMRaut-win-Setup.exe", "OMRaut-win-Portable.zip", "releases.win.json", "assets.win.json", "RELEASES")
foreach ($Name in $RequiredFiles) {
  if (-not (Test-Path -LiteralPath (Join-Path $FeedDir $Name) -PathType Leaf)) { throw "Required release asset is missing: $Name" }
}
$Packages = @(Get-ChildItem -LiteralPath $FeedDir -File -Filter "OMRaut-$Version-*.nupkg" | ForEach-Object FullName)
if ($Packages.Count -lt 1) { throw "No Velopack package found for version $Version." }

Write-Output "Files that will be committed and pushed:"
& git add -A
if ($LASTEXITCODE -ne 0) { throw "Could not stage the release source changes." }
& git reset -- .venv-build/Scripts/omr-exam.exe .venv-build/Scripts/omr-harness.exe | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not exclude generated virtual-environment launchers from the commit." }
& git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw "Staged source has whitespace errors; fix them before publishing." }
& git diff --cached --name-status
$Confirmation = Read-Host "Type PUBLISH to commit these files, push main and $Tag, and publish the public GitHub release"
if ($Confirmation -cne "PUBLISH") { throw "Stopped before pushing or publishing. The version bump and staged files are retained." }

& git commit -m "Prepare OMRaut $Version release"
if ($LASTEXITCODE -ne 0) { throw "Could not commit release source changes." }
& git push $Remote $Branch
if ($LASTEXITCODE -ne 0) { throw "Could not push $Branch. The local release commit is retained." }
& git tag -a $Tag -m "OMRaut $Version"
if ($LASTEXITCODE -ne 0) { throw "Could not create local tag $Tag." }
& git push $Remote $Tag
if ($LASTEXITCODE -ne 0) { throw "Could not push $Tag. The source commit is already on GitHub." }

$Assets = @(
  (Join-Path $FeedDir "OMRaut-win-Setup.exe"),
  (Join-Path $FeedDir "OMRaut-win-Portable.zip")
) + $Packages + @(
  (Join-Path $FeedDir "releases.win.json"),
  (Join-Path $FeedDir "assets.win.json"),
  (Join-Path $FeedDir "RELEASES")
)
& gh release create $Tag @Assets --repo $Repo --verify-tag --title "OMRaut $Version" `
  --notes "Windows installer, portable app, and OTA update feed for OMRaut $Version."
if ($LASTEXITCODE -ne 0) {
  throw "Release assets were not published. Source and tag are on GitHub; recover with: gh release create $Tag <asset paths> --repo $Repo --verify-tag"
}
& gh release view $Tag --repo $Repo
if ($LASTEXITCODE -ne 0) { throw "Release was submitted, but could not be verified with gh." }
Write-Output "Published: https://github.com/$Repo/releases/tag/$Tag"
