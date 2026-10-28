param(
    [string]$EnvironmentFile = (Join-Path $PSScriptRoot '.env')
)

$ErrorActionPreference = 'Stop'
$composeFile = Join-Path $PSScriptRoot 'compose.yml'
$resolvedEnv = [System.IO.Path]::GetFullPath($EnvironmentFile)
if (-not (Test-Path -LiteralPath $resolvedEnv -PathType Leaf)) {
    throw "缺少部署配置：$resolvedEnv。请复制 deploy/.env.example 并填写真实配置。"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 docker 命令。'
}

$values = @{}
foreach ($line in Get-Content -LiteralPath $resolvedEnv) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)$') {
        $values[$Matches[1]] = $Matches[2].Trim()
    }
}
$databaseUrl = if ($env:DATABASE_URL) { $env:DATABASE_URL } else { $values['DATABASE_URL'] }
$jwtSecret = if ($env:JWT_SECRET) { $env:JWT_SECRET } else { $values['JWT_SECRET'] }
if (-not $databaseUrl -or
    $databaseUrl -notmatch '^mysql\+pymysql://' -or
    $databaseUrl -match 'CHANGE_ME|@localhost:|@127\.0\.0\.1:') {
    throw 'DATABASE_URL 必须是容器可访问的真实外部 MySQL 地址，不能使用占位符或容器本机地址。'
}
if (-not $jwtSecret -or
    $jwtSecret.Length -lt 32 -or
    $jwtSecret -match 'CHANGE_ME') {
    throw 'JWT_SECRET 必须是至少 32 字符的随机密钥。'
}
$httpPort = 8080
if ($env:HTTP_PORT) {
    $httpPort = [int]$env:HTTP_PORT
} elseif ($values.ContainsKey('HTTP_PORT')) {
    $httpPort = [int]$values['HTTP_PORT']
}
if ($httpPort -lt 1 -or $httpPort -gt 65535) {
    throw 'HTTP_PORT 必须在 1 到 65535 之间。'
}

$composeArgs = @('compose', '--env-file', $resolvedEnv, '-f', $composeFile)
& docker @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose 配置校验失败。' }
& docker @composeArgs build
if ($LASTEXITCODE -ne 0) { throw '镜像构建失败。' }
& docker @composeArgs run --rm --no-deps backend alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败；服务未更新。' }
& docker @composeArgs up -d --wait
if ($LASTEXITCODE -ne 0) { throw '服务启动或健康检查失败。' }

$healthUrl = "http://127.0.0.1:$httpPort"
$frontend = Invoke-WebRequest -UseBasicParsing -Uri "$healthUrl/healthz" -TimeoutSec 10
$backend = Invoke-WebRequest -UseBasicParsing -Uri "$healthUrl/api/health" -TimeoutSec 10
if ($frontend.StatusCode -ne 200 -or $backend.StatusCode -ne 200) {
    throw '部署冒烟测试失败。'
}
Write-Output "部署成功：$healthUrl"
