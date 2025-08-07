# generate_version.ps1
param (
    [string]$OutputFile = "src/version.py"
)

# 1. ��鵱ǰ�ύ�Ƿ��� Git ��ǩ
$tag = git describe --tags --exact-match 2>$null

if ($tag) {
    $version = $tag
} else {
    # 2. ��ȡ��ǰ��֧����ı�ǩ
    $latestTag = git describe --tags --abbrev=0 2>$null
    $commitHash = (git rev-parse --short HEAD)
    $timestamp = Get-Date -Format "yyyyMMdd-HHmm"

    if ($latestTag) {
        $version = "$latestTag-$timestamp-$commitHash"
    } else {
        # 3. ���û���κα�ǩ�����Դ� version.py ��ȡ�汾��
        $version = "v0.0.0"  # Ĭ�ϰ汾��

        if (Test-Path $OutputFile) {
            try {
                $content = Get-Content -Path $OutputFile -Raw
                if ($content -match 'VERSION\s*=\s*"([^"-]*)') {
                    $version = $matches[1]
                    Write-Host "ʹ�� version.py �еİ汾��: $version"
                } else {
                    Write-Host "version.py ��δ�ҵ� VERSION ������ʹ��Ĭ�ϰ汾 v0.0.0"
                }
            } catch {
                Write-Host "��ȡ version.py ʱ��������ʹ��Ĭ�ϰ汾 v0.0.0"
            }
        } else {
            Write-Host "version.py �����ڣ�ʹ��Ĭ�ϰ汾 v0.0.0"
        }
        # ������ version.py ����Ĭ�ϣ���Ҫ����ʱ����͹�ϣ
        $version = "$version-$timestamp-$commitHash"
    }
}

# д��汾�ŵ�ָ���ļ���Ĭ��Ϊ version.py��
Set-Content -Path $OutputFile -Value "VERSION = `"$version`""