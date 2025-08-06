# generate_version.ps1
param (
    [string]$OutputFile = "src/version.py"
)

# ��鵱ǰ�ύ�Ƿ��� Git ��ǩ
$tag = git describe --tags --exact-match 2>$null

if ($tag) {
    $version = $tag
} else {
        # 2. ���û�б�ǩ�����Դ� version.py ��ȡ�汾��
    $version = "v0.0.0"  # Ĭ�ϰ汾��

    # ��� version.py �Ƿ����
    if (Test-Path $OutputFile) {
        try {
            # ��ȡ�ļ�����
            $content = Get-Content -Path $OutputFile -Raw

            # ʹ��������ʽ��ȡ VERSION ��ֵ
            if ($content -match 'VERSION\s*=\s*"([^"-]*)') {
                $version = $matches[1]
                Write-Host "Using version from version.py: $version"
            } else {
                Write-Host "version.py ��δ�ҵ� VERSION ������ʹ��Ĭ�ϰ汾 v0.0.0"
            }
        } catch {
            Write-Host "��ȡ version.py ʱ��������ʹ��Ĭ�ϰ汾 v0.0.0"
        }
    } else {
        Write-Host "version.py �����ڣ�ʹ��Ĭ�ϰ汾 v0.0.0"
    }
    $commitHash = (git rev-parse --short HEAD)
    $timestamp = Get-Date -Format "yyyyMMdd-HHmm"
    $version = "$version-$timestamp-$commitHash"
}

# д��汾�ŵ�ָ���ļ���Ĭ��Ϊ version.py��
Set-Content -Path $OutputFile -Value "VERSION = `"$version`""