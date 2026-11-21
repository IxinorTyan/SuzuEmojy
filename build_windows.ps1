[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist/windows-packages'),
    [string]$FfmpegDirectory = (Join-Path $PSScriptRoot 'dist/ffmpeg-minimal'),
    [ValidateSet('system-runtime', 'self-contained', 'desktop-runtime')]
    [string]$Mode = 'system-runtime',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
[xml]$suzuProject = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'win_frontend/SuzuEmojy.Windows.csproj')
$suzuVersion = $suzuProject.SelectSingleNode('/Project/PropertyGroup/Version').InnerText
if ($suzuVersion -cnotmatch '^\d+\.\d+\.\d+-preview(?:\.\d+)?$') { throw 'Only preview ZIPs are built here.' }
$suzuName = "SuzuEmojy-Windows-$suzuVersion-win-x64-$Mode"
$suzuDestination = Join-Path $OutputDirectory $suzuName
$suzuDependencies = Join-Path $PSScriptRoot 'dist'
$suzuFfmpeg = Join-Path $FfmpegDirectory 'ffmpeg.exe'
$suzuFfmpegSources = 'SuzuEmojy-FFmpeg-sources.tar'
if ($DryRun) {
    Write-Output (Join-Path $suzuDestination "$suzuName.zip")
    Write-Output "FFmpeg: $suzuFfmpeg"
    return
}
if (Test-Path -LiteralPath $suzuDestination) { throw "Output already exists: $suzuDestination" }
$suzuRecipe = Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'scripts/build_ffmpeg_minimal.sh')
foreach ($suzuFile in @('recipe.sha256', 'ffmpeg.exe', 'THIRD_PARTY.txt', $suzuFfmpegSources)) {
    if (!(Test-Path -LiteralPath (Join-Path $FfmpegDirectory $suzuFile) -PathType Leaf)) { throw "Missing minimal FFmpeg file: $suzuFile" }
}
if ((Get-Content -Raw -LiteralPath (Join-Path $FfmpegDirectory 'recipe.sha256')).Trim() -ne $suzuRecipe.Hash) {
    throw 'FFmpeg was not built with the current minimal recipe.'
}

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
$suzuDestination = Join-Path $suzuOutput $suzuName
$suzuStage = (New-Item -ItemType Directory -Path (Join-Path $suzuOutput ('.wb-' + [guid]::NewGuid().ToString('N').Substring(0, 12)))).FullName
$suzuPortable = Join-Path $suzuStage 'app'
$suzuApplication = Join-Path $suzuPortable 'bin'
$suzuResult = (New-Item -ItemType Directory -Path (Join-Path $suzuStage 'output')).FullName
try {
    $suzuTarget = if ($Mode -eq 'system-runtime') { @('-p:UseSystemWindowsRuntime=true') }
        else { @('-r', 'win-x64', '--self-contained', ($Mode -eq 'self-contained').ToString().ToLowerInvariant()) }
    $suzuPublish = @('publish', (Join-Path $PSScriptRoot 'win_frontend/SuzuEmojy.Windows.csproj'), '-c', 'Release') + $suzuTarget +
        @('-p:DebugType=None', '-p:DebugSymbols=false', '-o', $suzuApplication)
    & dotnet @suzuPublish
    if ($LASTEXITCODE -ne 0) { throw 'Windows build failed.' }
    $suzuLauncher = Join-Path $suzuStage 'launcher'
    & dotnet build (Join-Path $PSScriptRoot 'win_launcher/SuzuEmojy.Launcher.csproj') -c Release -o $suzuLauncher -p:DebugType=None -p:DebugSymbols=false
    if ($LASTEXITCODE -ne 0) { throw 'Portable launcher build failed.' }
    Copy-Item -LiteralPath (Join-Path $suzuLauncher 'SuzuEmojy.exe') -Destination $suzuPortable
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
    $suzuMarker | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $suzuApplication 'windows-preview.json')
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
    foreach ($suzuItem in Get-ChildItem -LiteralPath $suzuPortable -Recurse -Force) {
        if (($suzuItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $suzuItem.Extension -in @('.py', '.pyw', '.pyc', '.pyd', '.whl') -or
            $suzuItem.Name -match '^(python|pyside|pyqt|qt6|backend$|data$)') { throw "Unexpected package entry: $($suzuItem.Name)" }
    }
    $suzuZip = Join-Path $suzuResult "$suzuName.zip"
    [IO.Compression.ZipFile]::CreateFromDirectory($suzuPortable, $suzuZip, [IO.Compression.CompressionLevel]::Optimal, $false)
    $suzuBytes = (Get-Item -LiteralPath $suzuZip).Length
    if ($Mode -eq 'system-runtime' -and $suzuBytes -gt 20000000) { throw "ZIP exceeds 20 MB: $suzuBytes bytes." }
    "$( (Get-FileHash -LiteralPath $suzuZip).Hash.ToLowerInvariant() )  $suzuName.zip" |
        Set-Content -Encoding ascii -LiteralPath "$suzuZip.sha256"
    Copy-Item -LiteralPath (Join-Path $FfmpegDirectory $suzuFfmpegSources) -Destination $suzuResult
    [IO.Directory]::Move($suzuResult, $suzuDestination)
    Write-Output "$(Join-Path $suzuDestination "$suzuName.zip") ($suzuBytes bytes)"
}
finally {
    $suzuStageFull = [IO.Path]::GetFullPath($suzuStage)
    if ([IO.Path]::GetDirectoryName($suzuStageFull) -eq $suzuOutput -and
        [IO.Path]::GetFileName($suzuStageFull) -match '^\.wb-[0-9a-f]{12}$') {
        Remove-Item -LiteralPath $suzuStageFull -Recurse -Force
    }
}
