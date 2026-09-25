# Runs on GitHub Actions windows-latest. End users receive MoneyEngine-Windows.zip.
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Build this package on Windows.' }
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'Node.js is needed on the GitHub Windows build machine.'
}
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
if (-not (Test-Path 'frontend/dist/index.html')) { throw 'Frontend index.html was not built' }

if (-not (Test-Path '.build-venv/Scripts/python.exe')) {
    python -m venv .build-venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is needed on the GitHub Windows build machine.' }
}
$python = Join-Path (Get-Location) '.build-venv/Scripts/python.exe'
& $python -m pip install -r backend/requirements.txt -r packaging/requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed' }
& $python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Project tests failed' }
& $python -m PyInstaller MoneyEngine.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$package = Join-Path (Get-Location) 'dist/MoneyEngine-Windows'
$executable = Join-Path $package 'MoneyEngine.exe'
if (-not (Test-Path $executable)) { throw 'MoneyEngine.exe is missing from the package' }
if (-not (Test-Path (Join-Path $package '_internal/frontend/dist/index.html'))) {
    throw 'The built frontend is missing from the package'
}
New-Item -ItemType Directory -Force (Join-Path $package 'data'), (Join-Path $package 'logs') | Out-Null
Set-Content -Path (Join-Path $package 'data/README.txt') -Value 'SQLite, encrypted settings, and generated images are stored here. Keep this folder when updating.' -Encoding UTF8
Set-Content -Path (Join-Path $package 'logs/README.txt') -Value 'Startup and server errors are written to money-engine.log here.' -Encoding UTF8
& $python packaging/smoke_windows.py $executable
if ($LASTEXITCODE -ne 0) { throw 'Packaged app smoke test failed' }
Copy-Item 'packaging/사용방법.txt' (Join-Path $package '사용방법.txt')

$output = Join-Path (Get-Location) 'dist/MoneyEngine-Windows.zip'
if (Test-Path $output) { Remove-Item $output }
Compress-Archive -Path $package -DestinationPath $output
Add-Type -AssemblyName System.IO.Compression
$zip = [System.IO.Compression.ZipFile]::OpenRead($output)
try {
    $entries = @($zip.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
    foreach ($required in @(
        'MoneyEngine-Windows/MoneyEngine.exe',
        'MoneyEngine-Windows/_internal/frontend/dist/index.html',
        'MoneyEngine-Windows/data/README.txt',
        'MoneyEngine-Windows/logs/README.txt'
    )) {
        if ($entries -notcontains $required) { throw "ZIP is missing $required" }
    }
} finally { $zip.Dispose() }
Write-Host "Windows package ready: $output"
