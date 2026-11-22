[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [string]$FfmpegDirectory,
    [string]$Version,
    [ValidateSet('system-runtime', 'self-contained', 'desktop-runtime')]
    [string]$Mode = 'system-runtime',
    [switch]$DryRun,
    [switch]$Interactive,
    [switch]$NoOpen
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot 'dist/windows-packages' }
if (-not $FfmpegDirectory) { $FfmpegDirectory = Join-Path $PSScriptRoot 'dist/ffmpeg-minimal' }
$suzuFfmpegSources = 'SuzuEmojy-FFmpeg-sources.tar'

function Get-FfmpegPackageProblem([string]$Directory, [string]$RecipeHash) {
    foreach ($file in @('recipe.sha256', 'ffmpeg.exe', 'THIRD_PARTY.txt', $suzuFfmpegSources)) {
        $path = Join-Path $Directory $file
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return "Missing minimal FFmpeg file: $path" }
    }
    if ((Get-Content -Raw -LiteralPath (Join-Path $Directory 'recipe.sha256')).Trim() -ne $RecipeHash) {
        return "FFmpeg was not built with the current minimal recipe: $Directory"
    }
}

function Find-InteractiveFfmpeg([string]$DefaultDirectory, [string]$RecipeHash) {
    if (-not (Get-FfmpegPackageProblem $DefaultDirectory $RecipeHash)) { return $DefaultDirectory }
    # Check nearby SuzuEmojy checkouts only (one and two levels up).
    $root = (Get-Item -LiteralPath $PSScriptRoot).Parent
    for ($level = 0; $level -lt 2 -and $null -ne $root; $level++) {
        foreach ($checkout in (Get-ChildItem -LiteralPath $root.FullName -Filter 'SuzuEmojy*' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name)) {
            $candidate = Join-Path $checkout.FullName 'dist/ffmpeg-minimal'
            if (-not (Get-FfmpegPackageProblem $candidate $RecipeHash)) { return $candidate }
        }
        $root = $root.Parent
    }
    if ($DryRun) { return $DefaultDirectory }
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    try {
        $picker.Description = 'Select the built ffmpeg-minimal folder (not the FFmpeg executable).'
        $picker.ShowNewFolderButton = $false
        $picker.SelectedPath = $PSScriptRoot
        if ($picker.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $picker.SelectedPath }
    } finally { $picker.Dispose() }
    throw 'No FFmpeg folder selected. To build it first, see docs/ffmpeg-minimal.md.'
}

function New-PortableArchive([string]$SourceDirectory, [string]$ZipPath) {
    $prefix = [IO.Path]::GetFullPath($SourceDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $files = foreach ($item in Get-ChildItem -LiteralPath $SourceDirectory -Recurse -Force) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $item.Extension -in @('.py', '.pyw', '.pyc', '.pyd', '.whl') -or
            $item.Name -match '^(python|pyside|pyqt|qt6|backend$|data$)') { throw "Unexpected package entry: $($item.Name)" }
        if (-not $item.PSIsContainer) { $item }
    }
    $archive = [IO.Compression.ZipFile]::Open($ZipPath, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $files) {
            # Windows PowerShell's ZipFile.CreateFromDirectory can use backslashes;
            # the release layout requires portable, forward-slash entry names.
            $entryName = $file.FullName.Substring($prefix.Length).Replace('\', '/')
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file.FullName, $entryName,
                [IO.Compression.CompressionLevel]::Optimal)
        }
    } finally { $archive.Dispose() }
}

[xml]$suzuProject = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'win_frontend/SuzuEmojy.Windows.csproj')
$suzuProjectVersion = $suzuProject.SelectSingleNode('/Project/PropertyGroup/Version').InnerText
if ($Interactive -and -not $Version) {
    $suzuDefaultVersion = $suzuProjectVersion -replace '-preview(?:\.\d+)?$', ''
    $Version = (Read-Host "Package version [$suzuDefaultVersion]").Trim()
    if (-not $Version) { $Version = $suzuDefaultVersion }
}
$suzuVersion = if ($Version) { $Version } else { $suzuProjectVersion }
if ($suzuVersion -cnotmatch '^2\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-preview(?:\.\d+)?)?$') { throw 'Expected a Windows 2.x stable or preview version.' }
$suzuName = "SuzuEmojy-Windows-$suzuVersion-win-x64-$Mode"
$suzuFolderName = $suzuName
if (Test-Path -LiteralPath (Join-Path $OutputDirectory $suzuFolderName)) {
    if ($Interactive) { $suzuFolderName += '-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') }
    elseif (-not $DryRun) { throw "Output already exists: $(Join-Path $OutputDirectory $suzuFolderName)" }
}
$suzuDestination = Join-Path $OutputDirectory $suzuFolderName
$suzuDependencies = Join-Path $PSScriptRoot 'dist'
$suzuRecipeHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'scripts/build_ffmpeg_minimal.sh')).Hash
if ($Interactive -and -not $PSBoundParameters.ContainsKey('FfmpegDirectory')) {
    $FfmpegDirectory = Find-InteractiveFfmpeg $FfmpegDirectory $suzuRecipeHash
}
$suzuFfmpeg = Join-Path $FfmpegDirectory 'ffmpeg.exe'
if ($DryRun) {
    Write-Output (Join-Path $suzuDestination "$suzuName.zip")
    Write-Output "FFmpeg: $suzuFfmpeg"
    return
}
$suzuFfmpegProblem = Get-FfmpegPackageProblem $FfmpegDirectory $suzuRecipeHash
if ($suzuFfmpegProblem) { throw $suzuFfmpegProblem }
Write-Host "Using FFmpeg: $suzuFfmpeg"

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$suzuWebp = Join-Path $suzuDependencies 'webp-decoder'
$suzuPalette = Join-Path $suzuDependencies 'legacy-palette'
if (!(Test-Path -LiteralPath (Join-Path $suzuWebp 'suzu-webp.dll'))) { & (Join-Path $PSScriptRoot 'scripts/build_webp_decoder.ps1') }
if (!(Test-Path -LiteralPath (Join-Path $suzuPalette 'suzu-legacy-palette.dll'))) { & (Join-Path $PSScriptRoot 'scripts/build_legacy_palette.ps1') }
$suzuCache = (New-Item -ItemType Directory -Force -Path (Join-Path $suzuDependencies 'windows-package-cache')).FullName
# Use the pinned C ABI DLL and its license, not the wheel wrappers or an interpreter.
$suzuLottieUrl = 'https://files.pythonhosted.org/packages/f9/ca/9b47a54a0553ba4ee1eb57ad279c23ce4e01c30638da5f2a1e4bee4dc357/rlottie_python-1.3.8-cp37-abi3-win_amd64.whl'
$suzuLottieHash = 'C89F0701FE207D734B6E669F9A4C8779B2D5CD6A015BAA59023A66A4F3923EC1'
$suzuLottie = Join-Path $suzuCache 'rlottie-1.3.8-win-x64.zip'
if (!(Test-Path -LiteralPath $suzuLottie) -or (Get-FileHash -LiteralPath $suzuLottie).Hash -ne $suzuLottieHash) {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 45 -Uri $suzuLottieUrl -OutFile $suzuLottie
}
if ((Get-FileHash -LiteralPath $suzuLottie).Hash -ne $suzuLottieHash) { throw 'Unexpected rlottie archive SHA-256.' }

$suzuOutput = (New-Item -ItemType Directory -Force -Path $OutputDirectory).FullName
$suzuDestination = Join-Path $suzuOutput $suzuFolderName
$suzuStage = (New-Item -ItemType Directory -Path (Join-Path $suzuOutput ('.wb-' + [guid]::NewGuid().ToString('N').Substring(0, 12)))).FullName
$suzuPortable = Join-Path $suzuStage 'app'
$suzuApplication = Join-Path $suzuPortable 'bin'
$suzuResult = (New-Item -ItemType Directory -Path (Join-Path $suzuStage 'output')).FullName
try {
    $suzuTarget = if ($Mode -eq 'system-runtime') { @('-p:UseSystemWindowsRuntime=true') }
        else { @('-r', 'win-x64', '--self-contained', ($Mode -eq 'self-contained').ToString().ToLowerInvariant()) }
    $suzuPublish = @('publish', (Join-Path $PSScriptRoot 'win_frontend/SuzuEmojy.Windows.csproj'), '-c', 'Release') + $suzuTarget +
        @("-p:Version=$suzuVersion", '-p:DebugType=None', '-p:DebugSymbols=false', '-o', $suzuApplication)
    & dotnet @suzuPublish
    if ($LASTEXITCODE -ne 0) { throw 'Windows build failed.' }
    $suzuLauncher = Join-Path $suzuStage 'launcher'
    & dotnet build (Join-Path $PSScriptRoot 'win_launcher/SuzuEmojy.Launcher.csproj') -c Release -o $suzuLauncher -p:DebugType=None -p:DebugSymbols=false
    if ($LASTEXITCODE -ne 0) { throw 'Portable launcher build failed.' }
    Copy-Item -LiteralPath (Join-Path $suzuLauncher 'SuzuEmojy.exe') -Destination $suzuPortable
    if ($Mode -eq 'system-runtime') {
        $suzuUpdater = Join-Path $suzuStage 'updater'
        & dotnet build (Join-Path $PSScriptRoot 'win_updater/SuzuEmojy.Updater.csproj') -c Release -o $suzuUpdater -p:DebugType=None -p:DebugSymbols=false
        if ($LASTEXITCODE -ne 0) { throw 'Windows updater build failed.' }
        Copy-Item -LiteralPath (Join-Path $suzuUpdater 'SuzuEmojy.Updater.exe') -Destination $suzuApplication
    }
    foreach ($suzuFile in @('suzu-webp.dll', 'libwebp-COPYING.txt', 'libwebp-PATENTS.txt')) {
        Copy-Item -LiteralPath (Join-Path $suzuWebp $suzuFile) -Destination $suzuApplication
    }
    foreach ($suzuFile in @('suzu-legacy-palette.dll', 'Pillow-LICENSE.txt')) {
        Copy-Item -LiteralPath (Join-Path $suzuPalette $suzuFile) -Destination $suzuApplication
    }
    $suzuWheel = [IO.Compression.ZipFile]::OpenRead($suzuLottie)
    try {
        $suzuEntries = @{
            'rlottie_python/rlottie.dll' = 'rlottie.dll'
            'rlottie_python-1.3.8.dist-info/LICENSE' = 'rlottie-LICENSE.txt'
        }
        foreach ($suzuEntry in $suzuEntries.GetEnumerator()) {
            [IO.Compression.ZipFileExtensions]::ExtractToFile($suzuWheel.GetEntry($suzuEntry.Key), (Join-Path $suzuApplication $suzuEntry.Value))
        }
    } finally { $suzuWheel.Dispose() }
    $suzuCodecDirectory = (New-Item -ItemType Directory -Path (Join-Path $suzuApplication 'ffmpeg')).FullName
    Copy-Item -LiteralPath $suzuFfmpeg, (Join-Path $FfmpegDirectory 'THIRD_PARTY.txt') -Destination $suzuCodecDirectory
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'LICENSE') -Destination $suzuApplication
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'docs/windows-packaging.md') -Destination (Join-Path $suzuApplication 'Windows-README.md')
    $suzuMarker = [ordered]@{ version = $suzuVersion; architecture = 'win-x64'; runtime = $Mode; layout = 'portable-v2' }
    if ($Mode -eq 'system-runtime') { $suzuMarker['updateProtocol'] = 1 }
    [IO.File]::WriteAllText((Join-Path $suzuApplication 'windows-preview.json'), (ConvertTo-Json -InputObject $suzuMarker))
    $suzuSmokeBuild = Join-Path $suzuStage 'smoke-runner'
    $suzuSmokeApp = Join-Path $suzuStage 'smoke-app'
    $suzuTestProject = Join-Path $PSScriptRoot 'win_frontend_tests/SuzuEmojy.Windows.Tests.csproj'
    $suzuTestArguments = @('build', $suzuTestProject, '-c', 'Release', '-o', $suzuSmokeBuild)
    if ($Mode -eq 'system-runtime') { $suzuTestArguments += '-p:UseSystemWindowsRuntime=true' }
    & dotnet @suzuTestArguments
    if ($LASTEXITCODE -ne 0) { throw 'Package smoke build failed.' }
    Copy-Item -LiteralPath $suzuPortable -Destination $suzuSmokeApp -Recurse
    $suzuSmokeBin = Join-Path $suzuSmokeApp 'bin'
    Get-ChildItem -LiteralPath $suzuSmokeBuild -Filter 'SuzuEmojy.Windows.Tests.*' -File |
        Copy-Item -Destination $suzuSmokeBin
    & (Join-Path $suzuSmokeBin 'SuzuEmojy.Windows.Tests.exe') --package-smoke $suzuSmokeBin (Join-Path $suzuStage 'smoke-data')
    if ($LASTEXITCODE -ne 0) { throw 'Native package smoke failed.' }
    $suzuZip = Join-Path $suzuResult "$suzuName.zip"
    New-PortableArchive $suzuPortable $suzuZip
    $suzuBytes = (Get-Item -LiteralPath $suzuZip).Length
    if ($Mode -eq 'system-runtime' -and $suzuBytes -gt 20000000) { throw "ZIP exceeds 20 MB: $suzuBytes bytes." }
    if ($Mode -eq 'system-runtime') {
        & (Join-Path $suzuSmokeBin 'SuzuEmojy.Windows.Tests.exe') --verify-update-zip $suzuZip
        if ($LASTEXITCODE -ne 0) { throw 'ZIP is incompatible with the in-app updater.' }
    }
    "$( (Get-FileHash -LiteralPath $suzuZip).Hash.ToLowerInvariant() )  $suzuName.zip" |
        Set-Content -Encoding ascii -LiteralPath "$suzuZip.sha256"
    Copy-Item -LiteralPath (Join-Path $FfmpegDirectory $suzuFfmpegSources) -Destination $suzuResult
    [IO.Directory]::Move($suzuResult, $suzuDestination)
    Write-Output "$(Join-Path $suzuDestination "$suzuName.zip") ($suzuBytes bytes)"
} finally {
    $suzuStageFull = [IO.Path]::GetFullPath($suzuStage)
    if ([IO.Path]::GetDirectoryName($suzuStageFull) -eq $suzuOutput -and
        [IO.Path]::GetFileName($suzuStageFull) -match '^\.wb-[0-9a-f]{12}$') {
        Remove-Item -LiteralPath $suzuStageFull -Recurse -Force
    }
}

if ($Interactive -and -not $NoOpen) {
    try { Invoke-Item -LiteralPath $suzuDestination }
    catch { Write-Warning "Open this folder manually: $suzuDestination" }
}
