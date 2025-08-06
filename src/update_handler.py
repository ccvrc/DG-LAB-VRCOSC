# src/update_handler.py
import sys
import os
import aiohttp
import asyncio
import logging
import subprocess
import requests
import time
import re
from urllib.parse import urlparse, urlunparse
from packaging import version
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from i18n import translate as _

logger = logging.getLogger(__name__)

fallback_github_domain = "github.aqa.moe"
fallback_github_assets_domain = "release-assets.aqa.moe"

class UpdateHandler:
    location = "international"
    def __init__(self, current_version, config):
        self.current_version = current_version
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.update_url = "https://api.github.com/repos/ccvrc/DG-LAB-VRCOSC/releases"

    @classmethod
    def set_location(self, location):
        self.location = location

    
    async def check_update(self, manual_check=True):
        # 定义国内外的API地址
        domestic_url = "https://apigithub.aqa.moe/repos/ccvrc/DG-LAB-VRCOSC/releases"
        overseas_url = "https://api.github.com/repos/ccvrc/DG-LAB-VRCOSC/releases"
        
        try:
            self.location = await asyncio.to_thread(CheckLocationUsingHttp)
            logger.info(f"Current location: {self.location}")
            
            # 根据位置选择优先使用的URL
            if self.location == 'domestic':
                primary_url = domestic_url
                fallback_url = overseas_url 
                UpdateHandler.set_location("domestic")
                # logger.info(f"当前用户处于{self.location}")
                logger.info("当前用户处于国内，优先使用代理访问")
            else:
                primary_url = overseas_url
                fallback_url = domestic_url
                UpdateHandler.set_location("international")
                # logger.info(f"当前用户处于{self.location}")
                logger.info("当前用户处于国外，优先使用官方API")
            
            # 尝试第一个URL
            releases = None
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(primary_url) as response:
                        if response.status != 200:
                            raise ConnectionError(f"HTTP Error {response.status}")
                        logger.info(f"使用主要URL获取更新信息成功: {primary_url}")    
                        releases = await response.json()
            except Exception as e1:
                logger.info(f"主要URL访问失败: {e1}，尝试备用URL")
                if primary_url == domestic_url:
                    UpdateHandler.set_location("international")
                elif primary_url == overseas_url:
                    UpdateHandler.set_location("domestic")
                
                # 尝试第二个URL
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(fallback_url) as response:
                            if response.status != 200:
                                raise ConnectionError(f"HTTP Error {response.status}")
                            logger.info(f"使用备用URL获取更新信息成功: {fallback_url}")    
                            releases = await response.json()
                except Exception as e2:
                    # 两个URL都失败了
                    error_msg = f"主要URL失败: {e1}; 备用URL失败: {e2}"
                    logger.error(f"检查更新失败: {error_msg}")
                    if manual_check:
                        return {"available": False, "message": f"检查更新失败: {error_msg}"}
                    return None
            
            # 如果成功获取到releases数据，进行版本比较
            if releases:
                # 按版本排序
                valid_releases = [
                    r for r in releases 
                    if not r["prerelease"] and not r["draft"]
                ]
                latest_release = max(
                    valid_releases,
                    key=lambda x: version.parse(x['tag_name'])
                )
                # match = extract_version(self.current_version)
                # if match:
                # #     version1 = 'v' + match.group(1)
                #     print(f"Using version from version.py: {match}")
                # else:
                #     print("Failed to extract version from version.py")
                # 比较版本
                if version.parse(extract_version(latest_release['tag_name'])) > version.parse(extract_version(self.current_version)):
                    return {
                        "available": True,
                        "release_info": latest_release,
                        "current_version": self.current_version,
                        "latest_version": latest_release['tag_name']
                    }
                elif manual_check:
                    # 导入翻译函数

                    return {"available": False, "message": _('about_tab.already_latest_version')}
                    
        except Exception as e:
            logger.error(f"检查更新过程中出现未预期错误: {str(e)}")
            if manual_check:
                return {"available": False, "message": f"检查更新失败: {str(e)}"}


def extract_version(s):
    """
    从字符串中提取版本号部分，格式为 vX.X.X。
    
    Args:
        s (str): 输入字符串，例如 "v0.4.1-20250806-1827-8f0b6c8"。
    
    Returns:
        str: 提取的版本号，如 "v0.4.1"。如果未匹配到，返回 None。
    """
    match = re.match(r'^v\d+\.\d+\.\d+', s)
    return match.group(0) if match else None


def CheckLocationUsingHttp():
    """
    通过 HTTP 请求访问 www.google.com 判断当前用户是否处于国内或国外。
    返回 'domestic' 表示国内，'international' 表示国外。
    """
    url = 'https://www.google.com'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    try:
        response = requests.get(url, headers=headers, timeout=1)
        # 如果响应状态码为 200，认为可以访问
        if response.status_code == 200:
            return 'international'
        else:
            return 'domestic'
    except requests.exceptions.RequestException as e:
        # 捕获所有请求异常（如连接失败、超时、SSL 错误等）
        return 'domestic'

def get_redirected_url_with_new_domain(original_url, new_domain, timeout=10):
    """
    获取传入网址的重定向网址，并替换为指定的域名。

    Args:
        original_url (str): 原始URL。
        new_domain (str): 要替换的新域名（例如 "example.com"）。
        timeout (int): 请求超时时间（秒）。

    Returns:
        str: 替换域名后的重定向URL，若发生错误则返回None。
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    # 设置连接超时和读取超时
    timeout_tuple = (timeout, timeout)  # (连接超时, 读取超时)
    
    try:
        logger.info(f"正在请求: {original_url}")
        start_time = time.time()
        
        # 首先尝试使用 HEAD 请求，只获取头部信息
        logger.info("尝试使用 HEAD 请求...")
        response = requests.head(original_url, headers=headers, allow_redirects=True, timeout=timeout_tuple)
        final_url = response.url
        
        # 如果 HEAD 请求失败或没有重定向，尝试 GET 请求但使用 stream=True
        if not final_url or final_url == original_url:
            logger.info("HEAD 请求无效，尝试使用 GET 请求（流式）...")
            response = requests.get(original_url, headers=headers, allow_redirects=True, timeout=timeout_tuple, stream=True)
            final_url = response.url
            # 立即关闭连接，不下载内容
            response.close()

        elapsed_time = time.time() - start_time
        logger.info(f"请求完成，耗时: {elapsed_time:.2f}秒")
        logger.info(f"最终重定向URL: {final_url}")
        
        # 解析URL并替换域名
        parsed = urlparse(final_url)
        new_url = urlunparse(parsed._replace(netloc=new_domain))

        return new_url
    except requests.exceptions.ConnectTimeout:
        logger.info(f"连接超时（{timeout}秒）")
        return None
    except requests.exceptions.ReadTimeout:
        logger.info(f"读取超时（{timeout}秒）")
        return None
    except requests.exceptions.Timeout:
        logger.info(f"请求超时（{timeout}秒）")
        return None
    except requests.exceptions.RequestException as e:
        logger.info(f"请求失败: {e}")
        return None
    

def ReplaceDomain(original_url, new_domain):
    """
    替换 URL 中的域名。

    参数:
        original_url (str): 原始 URL。
        new_domain (str): 要替换的新域名。

    返回:
        str: 替换域名后的 URL。
    """
    parsed_url = urlparse(original_url)
    new_url = urlunparse(
        (
            parsed_url.scheme,       # 协议（http, https）
            new_domain,              # 新域名
            parsed_url.path,         # 路径
            parsed_url.params,       # 参数（如 ;param=value）
            parsed_url.query,        # 查询字符串（?key=value）
            parsed_url.fragment      # 片段（#anchor）
        )
    )
    return new_url

def get_download_url(release_info):
    """开始获取更新包链接"""
    logger.info("开始获取更新包链接")
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
        logger.info("已打开 PowerShell 窗口执行命令")
    except Exception as e:
        logger.info(f"执行失败: {e}")
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
        self.setWindowTitle(_('about_tab.discover_new_version'))
        layout = QVBoxLayout()
        
        # 版本信息
        version_label = QLabel(f"{_('about_tab.new_version')} {release_info['tag_name']}\n{_('about_tab.update')}：")
        layout.addWidget(version_label)
        
        # 更新内容
        content = QLabel(release_info['body'].replace('\r\n', '\n'))
        layout.addWidget(content)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        self.update_btn = QPushButton(_('about_tab.update_now'))
        self.cancel_btn = QPushButton(_('about_tab.cancel'))
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
        logger.info("开始更新。。。。。。。。。。。。。。。。。。。。。。。。。。。。。")
        download_url = get_download_url(release_info)
        location = UpdateHandler.location
        logger.info(f"Current location: {location}")
        if location == 'domestic':
            logger.info("当前用户处于国内，使用代理网址访问")
            download_url = ReplaceDomain(download_url, fallback_github_domain)
            logger.info(download_url)
            download_url = get_redirected_url_with_new_domain(download_url, fallback_github_assets_domain)
            logger.info(download_url)
        write_download_ps1_file()
        run_powershell_in_new_window(download_url)
        
    def cancel_download(self):
        self.cancelled = True
        self.reject()
        
    def is_cancelled(self):
        return self.cancelled

        
