param(
    [string]$OutputDirectory = "",
    [string]$Compiler = "gcc"
)
$ErrorActionPreference = 'Stop'
$suzuRoot = Split-Path -Parent $PSScriptRoot
if (!$OutputDirectory) { $OutputDirectory = Join-Path $suzuRoot 'dist/legacy-palette' }
$suzuOutput = (New-Item -ItemType Directory -Force -Path $OutputDirectory).FullName
$suzuSource = (New-Item -ItemType Directory -Force -Path (Join-Path $suzuOutput 'reference')).FullName
$suzuPillow = 'https://raw.githubusercontent.com/python-pillow/Pillow/13.0.0'
$suzuFiles = [ordered]@{
    'Quant.c' = 'DF6C7A4A5B43C0069728B44653511FEDBE7009161743582F036973D13F28B061'
    'QuantOctree.c' = 'FEF2AFF6405665326076238102BBC5B54488C381395B4D51A5BCCAF92F7AE94B'
    'QuantOctree.h' = '9F86DFB036C98EF7B97AD1708B6C293EF9B37994A4B7B4DBB635D19A1EE8FBEF'
    'QuantHeap.c' = 'F081751373A526557F9A6F832FD88C87D69F4880E7F1220657C37E6A3892D18F'
    'QuantHeap.h' = '82DEFD43870FB01D8D60FBFDEA1A6569D3BFF66A1FEE1FE91626A2F2ACA474AA'
    'QuantHash.c' = '7FF5AED41C79B4B3B0829974CE5BE3F55F48348FFD86F190B510B770A5ED5B6A'
    'QuantHash.h' = 'D3801625F19231C900266B9287503A3AAF1F09A74733E76282D4675D9E27E551'
    'QuantPngQuant.h' = '1DE96837819012F1EDC6DA33660CCD5E99322CAAA7A321306A511D43C933AD60'
    'QuantTypes.h' = '863351B8043BC0A47F9F79633E427D45D1B134B955880A31F4F9DAE668B559E7'
    'ImagingUtils.h' = '55A2A5AD26065921169C60D4F7ABB940BCC0D9BD5511F6FB053B9C96A49AA3C6'
    'Pillow-LICENSE.txt' = '15181E7363DCA9AED78B79BEBEBC7FDE7F1814B8BD311EA3B87AE8CCADFC185B'
}
foreach ($suzuFile in $suzuFiles.Keys) {
    $suzuDownload = Join-Path $suzuSource $suzuFile
    $suzuRelative = if ($suzuFile -eq 'Pillow-LICENSE.txt') { 'LICENSE' } else { "src/libImaging/$suzuFile" }
    if (!(Test-Path -LiteralPath $suzuDownload)) {
        Invoke-WebRequest -UseBasicParsing -Uri "$suzuPillow/$suzuRelative" -OutFile $suzuDownload -TimeoutSec 30
    }
    if ((Get-FileHash -LiteralPath $suzuDownload -Algorithm SHA256).Hash -ne $suzuFiles[$suzuFile]) {
        throw "Unexpected SHA-256 for $suzuFile"
    }
}
$suzuShim = Join-Path $suzuRoot 'native/legacy_palette'
$suzuOperands = @('-m64', '-std=c11', '-O2', '-ffunction-sections', '-fdata-sections', '-static', '-shared',
    '-Wl,--gc-sections,--exclude-all-symbols', '-s', "-I$suzuShim", "-I$suzuSource",
    (Join-Path $suzuShim 'suzu_quant.c'), (Join-Path $suzuSource 'QuantOctree.c'),
    (Join-Path $suzuSource 'QuantHeap.c'), (Join-Path $suzuSource 'QuantHash.c'),
    '-o', 'suzu-legacy-palette.dll')
# Keep the linker output relative so the workspace can contain non-ASCII characters.
Push-Location -LiteralPath $suzuOutput
try {
    & $Compiler @suzuOperands
    if ($LASTEXITCODE -ne 0) { throw 'Could not build legacy palette adapter.' }
} finally { Pop-Location }
Copy-Item -LiteralPath (Join-Path $suzuSource 'Pillow-LICENSE.txt') -Destination $suzuOutput
Get-Item -LiteralPath (Join-Path $suzuOutput 'suzu-legacy-palette.dll')
