$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    py -3.12 -m venv (Join-Path $PSScriptRoot '.build-venv')
}

& $venvPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies could not be installed.' }

$assets = @(
    'wool-orb.png', 'champagne-skin.png', 'champagne-alerts.png',
    'bitcoin.png', 'ethereum.png', 'arweave.png',
    'near.png', 'chainlink.png', 'ondo-finance.png'
)
$arguments = @('--noconfirm', '--clean', '--windowed', '--onedir',
    '--name', 'MaoqiuCryptoWatch', '--distpath', 'dist', '--workpath', 'build',
    '--specpath', 'build')
foreach ($file in $assets) {
    $assetPath = Join-Path (Join-Path $PSScriptRoot 'assets') $file
    $arguments += @('--add-data', "$assetPath;assets")
}
$arguments += 'app.py'
& $venvPython -m PyInstaller @arguments
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$bundle = Join-Path $PSScriptRoot 'dist\MaoqiuCryptoWatch'
$zip = Join-Path $PSScriptRoot 'dist\MaoqiuCryptoWatch-v1.0.0-windows-x64.zip'
if (-not (Test-Path -LiteralPath (Join-Path $bundle 'MaoqiuCryptoWatch.exe'))) {
    throw 'The expected EXE was not produced.'
}
if (Get-ChildItem -LiteralPath $bundle -Recurse -File | Where-Object {
    $_.Name -match '(?i)\.dpapi$|settings\.json$|market-cache\.json$|startup-error\.log$'
}) {
    throw 'Private runtime state was found in the distribution.'
}
Compress-Archive -Path (Join-Path $bundle '*') -DestinationPath $zip -Force
Write-Output $zip
