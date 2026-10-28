param(
    [string]$Origin = 'http://127.0.0.1:8080',
    [string]$EnvironmentFile = (Join-Path $PSScriptRoot '..\.env')
)

$ErrorActionPreference = 'Stop'
foreach ($path in @('/healthz', '/api/health')) {
    $response = Invoke-WebRequest -UseBasicParsing -Uri ($Origin.TrimEnd('/') + $path) -TimeoutSec 10
    if ($response.StatusCode -ne 200) { throw "$path 健康检查失败：$($response.StatusCode)" }
    Write-Output "$path OK"
}
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $compose = Join-Path $PSScriptRoot '..\compose.yml'
    & docker compose --env-file $EnvironmentFile -f $compose ps --format table
    if ($LASTEXITCODE -ne 0) { throw '无法读取容器状态。' }
}
