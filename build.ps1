$ErrorActionPreference = 'Stop'
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "prokjs's Set 19 TFT tool" --add-data "data/set19.json;data" --add-data "assets;assets" app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Built dist/prokjs's Set 19 TFT tool.exe"
