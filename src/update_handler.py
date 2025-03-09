# src/update_handler.py
import sys
import json
import os
import aiohttp
import asyncio
import logging
import zipfile
import subprocess
from pathlib import Path
from packaging import version
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout, QMessageBox, QApplication
from qasync import asyncSlot

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
    def handle_update_package(self, zip_path):
        try:
            exe_dir = os.path.dirname(sys.executable)
            
            # 解压文件
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # 验证压缩包结构
                if 'DG-LAB-VRCOSC.exe' not in zip_ref.namelist():
                    raise ValueError("无效的更新包")
                
                # 清空临时文件
                temp_dir = os.path.join(exe_dir, "update_temp")
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                os.makedirs(temp_dir)
                
                zip_ref.extractall(temp_dir)
            
            # 移动文件
            src_exe = os.path.join(temp_dir, 'DG-LAB-VRCOSC.exe')
            dst_exe = os.path.join(exe_dir, 'update.exe')
            if os.path.exists(dst_exe):
                os.remove(dst_exe)
            shutil.move(src_exe, dst_exe)
            
            # 创建更新脚本
            self.create_update_script(exe_dir)
            
            # 提示重启
            dialog = QMessageBox(
                QMessageBox.Information,
                "更新完成",
                "需要重启应用完成更新，是否立即重启？",
                QMessageBox.Yes | QMessageBox.No,
                self
            )
            if dialog.exec() == QMessageBox.Yes:
                self.launch_updater()
                
        except Exception as e:
            logger.error(f"更新处理失败: {str(e)}")
            QMessageBox.critical(None, "错误", f"更新处理失败: {str(e)}")
        
    async def start_download(self, release_info, parent_window):
        """开始下载更新包"""
        try:
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

            # 创建对话框
            dialog = UpdateDialog(parent_window, release_info)
            dialog.show()
            
            # 连接取消信号
            dialog.cancelled = False

            # 生成保存路径
            exe_path = os.path.dirname(sys.executable)
            zip_path = os.path.join(exe_path, "update.zip")
            
            last_progress = -1
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url) as response:
                    # 安全获取文件大小
                    total_size = max(int(response.headers.get('content-length', 1)), 1)
                    downloaded = 0

                    with open(zip_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(4096):  # 增大块大小
                            # 通过信号获取取消状态
                            if await asyncio.get_event_loop().run_in_executor(
                                None, dialog.is_cancelled
                            ):
                                f.close()
                                os.remove(zip_path)
                                return

                            f.write(chunk)
                            downloaded += len(chunk)
                            progress = min(int((downloaded / total_size) * 100), 100)
                            
            while not dialog.cancelled:
                # 更新进度条
                parent_window.progress.setValue(progress)
                await asyncio.sleep(0.1)
            # 下载完成处理
            if not dialog.cancelled:
                self.handle_update_package(zip_path)

        except asyncio.CancelledError:
            logger.info("下载已取消")

        except (aiohttp.ClientError, IOError, ValueError) as e:
            logger.error(f"下载失败: {str(e)}")
            QMessageBox.critical(self, "错误", f"下载更新失败: {str(e)}")
        except Exception as e:
            logger.exception("未处理的异常:")
            QMessageBox.critical(self, "错误", f"发生未知错误: {str(e)}")


    def create_update_script(self, exe_dir):
        """创建更新脚本"""
        bat_content = f"""@echo off
    :loop
    tasklist | find "DG-LAB-VRCOSC.exe" > nul
    if %errorlevel% == 0 (
        timeout /t 1 > nul
        goto loop
    )
    move /Y "{exe_dir}\\update.exe" "{exe_dir}\\DG-LAB-VRCOSC.exe"
    start "" "{exe_dir}\\DG-LAB-VRCOSC.exe"
    del "{exe_dir}\\update.bat"
    """
        bat_path = os.path.join(exe_dir, "update.bat")
        with open(bat_path, "w") as f:
            f.write(bat_content)

    def launch_updater(self):
        """启动更新程序"""
        exe_dir = os.path.dirname(sys.executable)
        bat_path = os.path.join(exe_dir, "update.bat")
        subprocess.Popen(bat_path, shell=True)
        QApplication.quit()



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

                # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        
        # 取消标志
        self.cancelled = False
        
        # 信号连接
        # self.update_btn.clicked.connect(self.start_download)
        self.cancel_btn.clicked.connect(self.cancel_download)

        # 修改信号连接方式
        # self.update_btn.clicked.connect(self._handle_update_clicked)

        # 添加中间处理层

        
        self.setLayout(layout)

    def start_download(self):
        self.progress.setVisible(True)
        self.update_btn.setEnabled(False)
        
    def cancel_download(self):
        self.cancelled = True
        self.reject()
        
    def is_cancelled(self):
        return self.cancelled
        
    def update_progress(self, value):
        self.progress.setValue(value)

    # def _handle_update_clicked(self):
    #     asyncio.create_task(self.on_update_clicked())

    # # 使用正确的异步处理
    # @asyncSlot()
    # async def on_update_clicked(self):
    #     try:
    #         # 原有的下载逻辑
    #         await self.start_download(self)
    #     except Exception as e:
    #         QMessageBox.critical(self, "错误", str(e))
        
