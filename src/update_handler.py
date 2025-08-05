# src/update_handler.py
import sys
import os
import aiohttp
import logging
import subprocess
from packaging import version
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

logger = logging.getLogger(__name__)

class UpdateHandler:
    def __init__(self, current_version, config):
        self.current_version = current_version
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.update_url = "https://api.github.com/repos/ccvrc/DG-LAB-VRCOSC/releases"
        
    async def check_update(self, manual_check=True):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.update_url) as response:
                    if response.status != 200:
                        raise ConnectionError(f"HTTP Error {response.status}")
                    print("获取更新信息成功")    
                    releases = await response.json()
                    
            # 按版本排序
            valid_releases = [
                r for r in releases 
                if not r["prerelease"] and not r["draft"]
            ]
            latest_release = max(
                valid_releases,
                key=lambda x: version.parse(x['tag_name'])
            )
            
            # 比较版本
            if version.parse(latest_release['tag_name']) > version.parse(self.current_version):
                return {
                    "available": True,
                    "release_info": latest_release,
                    "current_version": self.current_version,
                    "latest_version": latest_release['tag_name']
                }
            elif manual_check:
                return {"available": False, "message": "当前已是最新版本"}
                
        except Exception as e:
            logger.error(f"检查更新失败: {str(e)}")
            if manual_check:
                return {"available": False, "message": f"检查更新失败: {str(e)}"}

def get_download_url(release_info):
    """开始下载更新包"""
    # try:
    print("开始下载更新包")
    # 更安全的下载链接获取
    download_url = next(
        (asset["browser_download_url"] 
        for asset in release_info["assets"] 
        if asset["name"] == "DG-LAB-VRCOSC.zip"), 
        None
    )
    if not download_url:
        raise ValueError("未找到 DG-LAB-VRCOSC.zip 资源")
    return download_url


def run_powershell_in_new_window(download_url):
    # 获取当前脚本所在目录，适配打包后路径
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
        # 获取当前exe文件路径
    if getattr(sys, 'frozen', False):
        # 打包后的exe路径
        exe_path = os.path.dirname(sys.executable)
    else:
        # 开发环境路径
        exe_path = os.path.dirname(os.path.abspath(__file__))

    script_path = os.path.join(base_path, "download.ps1")
    url = download_url
    output_path = r"C:\temp"
    extract_path = r"D:\python"

    # 构建 PowerShell 命令参数列表
    command = [
        "powershell.exe",
        # "-NoExit",  # 保持窗口不关闭（可选）
        "-Command",
        f"& '{script_path}' -Url '{url}' -OutputPath '{output_path}' -ExtractPath '{exe_path}'"
    ]

    try:
        subprocess.Popen(
            command,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        print("已打开 PowerShell 窗口执行命令")
    except Exception as e:
        print(f"执行失败: {e}")
        sys.exit(1)
    finally:
        sys.exit(0)   

def write_download_ps1_file():
    """将指定的PowerShell脚本写入当前exe路径下的download.ps1文件"""
    # 获取当前脚本所在目录，适配打包后路径
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    # 构造目标文件路径
    file_path = os.path.join(base_path, 'download.ps1')
    
    # 要写入的PowerShell脚本内容
    ps1_content = '''param (
    [string]$Url = "https://github.com/ccvrc/DG-LAB-VRCOSC/releases/download/v0.3.0/DG-LAB-VRCOSC.zip",
    [string]$OutputPath = $env:TEMP,
    [string]$ExtractPath = $env:TEMP
)
# 确保路径存在
foreach ($path in ($OutputPath, $ExtractPath)) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}
# 使用 HttpClient 实现带进度条的下载
function Download-File {
    param (
        [string]$Url,
        [string]$OutputFile
    )
    try {
    Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
        # 创建 HttpClient 实例（推荐使用 using 语句管理生命周期）
        $httpClient = New-Object System.Net.Http.HttpClient
        $httpClient.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
        # 发送 HEAD 请求获取文件大小
        $headRequest = New-Object System.Net.Http.HttpRequestMessage -ArgumentList ([System.Net.Http.HttpMethod]::Head, $Url)
        $headResponse = $httpClient.SendAsync($headRequest).GetAwaiter().GetResult()
        $totalBytes = [double]$headResponse.Content.Headers.ContentLength
        # 验证文件大小
        if ($totalBytes -le 0) {
            throw "无法获取文件大小，请检查URL是否有效"
        }
        # 创建文件流
        $fileStream = [System.IO.File]::Create($OutputFile)
        # 发送 GET 请求
        $getRequest = New-Object System.Net.Http.HttpRequestMessage -ArgumentList ([System.Net.Http.HttpMethod]::Get, $Url)
        $response = $httpClient.SendAsync($getRequest, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        # 获取响应流
        $responseStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        # 创建缓冲区
        $buffer = New-Object byte[] 8192
        $downloadedBytes = 0
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $lastUpdate = [System.DateTime]::MinValue
        # 读取流并写入文件
        while ($true) {
            $bytesRead = $responseStream.Read($buffer, 0, $buffer.Length)
            if ($bytesRead -eq 0) { break }
            $fileStream.Write($buffer, 0, $bytesRead)
            $downloadedBytes += $bytesRead
            # 限制进度更新频率（每500ms更新一次）
            $currentTime = [System.DateTime]::Now
            if (($currentTime - $lastUpdate).TotalMilliseconds -gt 500) {
                $percentComplete = [math]::Min(100, [math]::Round(($downloadedBytes / $totalBytes) * 100, 2))
                $speed = ($downloadedBytes / 1MB) / [math]::Max(0.001, $stopwatch.Elapsed.TotalSeconds)
                Write-Progress -Activity "正在下载文件..." `
                    -Status ("进度: {0}% | 速度: {1:N2} MB/s | {2:N1}MB / {3:N1}MB" -f `
                        $percentComplete, `
                        $speed, `
                        ($downloadedBytes / 1MB), `
                        ($totalBytes / 1MB)) `
                    -PercentComplete $percentComplete
                $lastUpdate = $currentTime
            }
        }
        # 确保显示100%完成
        Write-Progress -Activity "正在下载文件..." -Status "下载完成" -Completed
    }
    finally {
        # 清理资源
        if ($null -ne $fileStream) { $fileStream.Dispose() }
        if ($null -ne $responseStream) { $responseStream.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
        if ($null -ne $httpClient) { $httpClient.Dispose() }
    }
}
# 解压 ZIP 文件（保持不变）
# 修改后的解压函数，使用 Expand-Archive 并添加 -Force 参数
function Extract-Zip {
    param (
        [string]$ZipFile,
        [string]$Destination
    )
    Expand-Archive -Path $ZipFile -DestinationPath $Destination -Force
}
# 主执行流程
try {
    $outputFile = Join-Path $OutputPath "DG-LAB-VRCOSC.zip"
    Write-Host "开始下载安装包..."
    Download-File -Url $Url -OutputFile $outputFile
    Write-Host "下载完成，开始安装..."
    Extract-Zip -ZipFile $outputFile -Destination $ExtractPath
    Write-Host "安装完成！安装位置: $ExtractPath"
    # 运行解压后的 EXE
    # $exePath = Join-Path $ExtractPath "DG-LAB-VRCOSC.exe"
    # if (Test-Path $exePath) {
    #     Start-Process -FilePath $exePath
    # } else {
    #     Write-Host "未找到 DG-LAB-VRCOSC.exe"
    # }
    # 关闭 PowerShell 窗口
    Write-Host "\n安装已完成，按任意键关闭此窗口..."
    $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    exit
}
catch {
    Write-Error "操作失败: $_"
    exit 1
}'''

    # 以GBK编码写入文件
    with open(file_path, 'w', encoding='gbk') as file:
        file.write(ps1_content)

class UpdateDialog(QDialog):
    """更新对话框实现"""
    def __init__(self, parent, release_info):
        super().__init__(parent)
        self.setWindowTitle("发现新版本")
        layout = QVBoxLayout()
        
        # 版本信息
        version_label = QLabel(f"新版本 {release_info['tag_name']}\n更新内容：")
        layout.addWidget(version_label)
        
        # 更新内容
        content = QLabel(release_info['body'].replace('\r\n', '\n'))
        layout.addWidget(content)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        self.update_btn = QPushButton("立即更新")
        self.cancel_btn = QPushButton("取消")
        btn_layout.addWidget(self.update_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        # 取消标志
        self.cancelled = False
        
        # 信号连接
        self.update_btn.clicked.connect(lambda: self.start_download(release_info))
        self.cancel_btn.clicked.connect(self.cancel_download)
       
        self.setLayout(layout)

    def start_download(self, release_info):
        print("开始更新。。。。。。。。。。。。。。。。。。。。。。。。。。。。。")
        download_url = get_download_url(release_info)
        write_download_ps1_file()
        run_powershell_in_new_window(download_url)
        
    def cancel_download(self):
        self.cancelled = True
        self.reject()
        
    def is_cancelled(self):
        return self.cancelled

        
