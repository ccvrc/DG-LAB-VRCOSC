# generate_version.ps1
param (
    [string]$OutputFile = "src/version.py"
)

# 1. 检查当前提交是否是 Git 标签
$tag = git describe --tags --exact-match 2>$null

if ($tag) {
    $version = $tag
} else {
    # 2. 获取当前分支最近的标签
    $latestTag = git describe --tags --abbrev=0 2>$null
    $commitHash = (git rev-parse --short HEAD)
    $timestamp = Get-Date -Format "yyyyMMdd-HHmm"

    if ($latestTag) {
        $version = "$latestTag-$timestamp-$commitHash"
    } else {
        # 3. 如果没有任何标签，尝试从 version.py 读取版本号
        $version = "v0.0.0"  # 默认版本号

        if (Test-Path $OutputFile) {
            try {
                $content = Get-Content -Path $OutputFile -Raw
                if ($content -match 'VERSION\s*=\s*"([^"-]*)') {
                    $version = $matches[1]
                    Write-Host "使用 version.py 中的版本号: $version"
                } else {
                    Write-Host "version.py 中未找到 VERSION 变量，使用默认版本 v0.0.0"
                }
            } catch {
                Write-Host "读取 version.py 时发生错误，使用默认版本 v0.0.0"
            }
        } else {
            Write-Host "version.py 不存在，使用默认版本 v0.0.0"
        }
        # 无论是 version.py 还是默认，都要加上时间戳和哈希
        $version = "$version-$timestamp-$commitHash"
    }
}

# 写入版本号到指定文件（默认为 version.py）
Set-Content -Path $OutputFile -Value "VERSION = `"$version`""