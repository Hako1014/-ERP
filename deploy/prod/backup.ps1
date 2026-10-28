param(
    [string]$EnvironmentFile = (Join-Path $PSScriptRoot '..\.env'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\backups')
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command mysqldump -ErrorAction SilentlyContinue)) {
    throw '缺少 mysqldump，请安装 MySQL 客户端。'
}
$url = $env:DATABASE_URL
if (-not $url) {
    if (-not (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf)) {
        throw '缺少部署环境文件或 DATABASE_URL 环境变量。'
    }
    $line = Get-Content -LiteralPath $EnvironmentFile | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
    if ($line) { $url = $line.Substring('DATABASE_URL='.Length).Trim() }
}
if (-not $url -or $url -match 'CHANGE_ME' -or $url -notmatch '^mysql\+pymysql://') {
    throw 'DATABASE_URL 未配置为有效的 MySQL 连接。'
}
$uri = [Uri]$url
$userinfo = $uri.UserInfo.Split(':', 2)
if ($userinfo.Count -ne 2 -or -not $uri.AbsolutePath.Trim('/')) {
    throw 'DATABASE_URL 缺少用户名、密码或库名。'
}
$user = [Uri]::UnescapeDataString($userinfo[0])
$password = [Uri]::UnescapeDataString($userinfo[1])
$database = [Uri]::UnescapeDataString($uri.AbsolutePath.Trim('/'))
$hostName = if ($uri.Host -eq 'host.docker.internal') { '127.0.0.1' } else { $uri.Host }
$port = if ($uri.IsDefaultPort) { 3306 } else { $uri.Port }
$targetDir = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($targetDir) | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupFile = Join-Path $targetDir "$database-$timestamp.sql"
if (Test-Path -LiteralPath $backupFile) { throw "备份文件已存在：$backupFile" }
$oldMysqlPwd = $env:MYSQL_PWD
try {
    $env:MYSQL_PWD = $password
    & mysqldump --host=$hostName --port=$port --user=$user --single-transaction --routines --triggers --events --hex-blob --result-file=$backupFile $database
    if ($LASTEXITCODE -ne 0) { throw 'mysqldump 备份失败。' }
    $file = Get-Item -LiteralPath $backupFile
    if ($file.Length -eq 0) { throw '备份文件为空。' }
    $hash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash
    Write-Output "备份完成：$backupFile（$($file.Length) 字节，SHA256=$hash）"
} finally {
    if ($null -eq $oldMysqlPwd) { Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue }
    else { $env:MYSQL_PWD = $oldMysqlPwd }
}
