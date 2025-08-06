# generate_version.ps1
param (
    [string]$OutputFile = "src/version.py"
)

# 检查当前提交是否是 Git 标签
$tag = git describe --tags --exact-match 2>$null

if ($tag) {
    $version = $tag
} else {
        # 2. 如果没有标签，尝试从 version.py 读取版本号
    $version = "v0.0.0"  # 默认版本号

    # 检查 version.py 是否存在
    if (Test-Path $OutputFile) {
        try {
            # 读取文件内容
            $content = Get-Content -Path $OutputFile -Raw

            # 使用正则表达式提取 VERSION 的值
            if ($content -match 'VERSION\s*=\s*"([^"-]*)') {
                $version = $matches[1]
                Write-Host "Using version from version.py: $version"
            } else {
                Write-Host "version.py 中未找到 VERSION 变量，使用默认版本 v0.0.0"
            }
        } catch {
            Write-Host "读取 version.py 时发生错误，使用默认版本 v0.0.0"
        }
    } else {
        Write-Host "version.py 不存在，使用默认版本 v0.0.0"
    }
    $commitHash = (git rev-parse --short HEAD)
    $timestamp = Get-Date -Format "yyyyMMdd-HHmm"
    $version = "$version-$timestamp-$commitHash"
}

# 写入版本号到指定文件（默认为 version.py）
Set-Content -Path $OutputFile -Value "VERSION = `"$version`""