param(
    [string]$OutputDirectory = "",
    [string]$Compiler = "gcc"
)
$ErrorActionPreference = 'Stop'
$suzuRoot = Split-Path -Parent $PSScriptRoot
if (!$OutputDirectory) { $OutputDirectory = Join-Path $suzuRoot 'dist/webp-decoder' }
$suzuOutput = (New-Item -ItemType Directory -Force -Path $OutputDirectory).FullName
$suzuVersion = '1.6.0'
$suzuDigest = 'E4AB7009BF0629FD11982D4C2AA83964CF244CFFBA7347ECD39019A9E38C4564'
$suzuArchive = Join-Path $suzuOutput "libwebp-$suzuVersion.tar.gz"
$suzuSource = Join-Path $suzuOutput "libwebp-$suzuVersion"

if (!(Test-Path -LiteralPath $suzuArchive)) {
    Invoke-WebRequest -Uri "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-$suzuVersion.tar.gz" -OutFile $suzuArchive -TimeoutSec 60
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $suzuArchive).Hash -ne $suzuDigest) {
    throw 'Unexpected libwebp source SHA-256.'
}
tar.exe -xf $suzuArchive -C $suzuOutput
if ($LASTEXITCODE -ne 0) { throw 'Could not extract libwebp.' }

# The DEF file keeps only decode/demux symbols public. Unreachable code is stripped.
$suzuFolders = @('src/dec', 'src/demux', 'src/dsp', 'src/utils')
$suzuSources = foreach ($suzuFolder in $suzuFolders) {
    Get-ChildItem -LiteralPath (Join-Path $suzuSource $suzuFolder) -Filter '*.c' -File |
        Where-Object Name -NotLike '*_test.c' |
        ForEach-Object FullName
}
$suzuDll = Join-Path $suzuOutput 'suzu-webp.dll'
$suzuFlags = @('-m64', '-std=c11', '-O2', '-fno-ident', '-ffunction-sections', '-fdata-sections', '-static', '-shared',
    '-Wl,--gc-sections,--exclude-all-symbols', '-s', '-DWEBP_USE_THREAD', "-I$suzuSource")
& $Compiler @suzuFlags @suzuSources (Join-Path $PSScriptRoot 'webp-decoder.def') '-o' $suzuDll
if ($LASTEXITCODE -ne 0) { throw 'Could not build the WebP decoder.' }
foreach ($suzuFile in @('COPYING', 'PATENTS')) {
    Copy-Item -LiteralPath (Join-Path $suzuSource $suzuFile) -Destination (Join-Path $suzuOutput "libwebp-$suzuFile.txt")
}
Get-Item -LiteralPath $suzuDll
