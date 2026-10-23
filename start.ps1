param([int]$Port = 8000, [switch]$Lan)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать Python-окружение.' }
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
}
$listenAddress = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
Write-Host "Текстом: http://127.0.0.1:$Port"
if ($Lan) { Write-Host 'Для телефона открой http://<IP этого компьютера>:'$Port' в той же Wi-Fi-сети.' }
& $python -m uvicorn backend.app:app --host $listenAddress --port $Port
