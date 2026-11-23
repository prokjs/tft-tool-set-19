$ErrorActionPreference = 'Stop'
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
npm ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run build:web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python tools/bundle_rust_notices.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run 'tauri:build' '--' '--no-bundle'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$destination = "dist/rust-0.7.5"
New-Item -ItemType Directory -Force $destination | Out-Null
Copy-Item "target/release/tft-desktop.exe" "$destination/prokjs's Set 19 TFT tool.exe"
Copy-Item "THIRD_PARTY_NOTICES.txt","LEGAL.md" $destination
Write-Host "Built $destination/prokjs's Set 19 TFT tool.exe"
