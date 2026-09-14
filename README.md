# **DG-LAB-VRCOSC**

这是与 **VRChat** 游戏联动的郊狼 (DG-LAB) **3.0** 设备控制程序，通过 VRChat 游戏内的 avatars 互动和其他事件来控制设备的输出。

- **兼容设备**：通过 WebSocket 控制 DG-LAB APP，目前只适配 DG-LAB 3.0 主机。

- **VRChat Avatar 联动功能** ( **OSC**)：

  - **面板控制模式**：通过 VRSuya 的 [SoundPad](https://booth.pm/zh-cn/items/5950846) 进行控制，映射按键到设备功能。同时也支持**远程控制**，你可以通过自己 avatar 上的面板控制其他安装相同面板玩家的设备。

  - **交互控制模式**：支持通过 VRChat 的 Contact 或 Physbones 参数进行控制，让 avatar 之间的交互可以控制设备输出（ 例如触碰或是拉伸动骨）。

  - **ChatBox 显示**：可以通过 VRChat 的 ChatBox 显示当前设备信息。

- [**Terrors of Nowhere**](https://terror.moe/) 游戏联动功能：

  - 游戏内受到伤害会增加设备输出，游戏内死亡会触发死亡惩罚。
  - 通过 [ToNSaveManager](https://github.com/ChrisFeline/ToNSaveManager) 的 WebSocket API 监控游戏事件，需要在游玩 ToN 时运行这个存档软件，并打开设置中的 WebSocket API 服务器。

**补充说明：**

- 面板控制功能需要在 Booth 购买 [声音面板](https://booth.pm/zh-cn/items/5950846) 后将资源导入工程，再导入本项目提供的修改包，将修改包内提供的 prefab 安装到您的 avatar 中。此处的修改包发布已获取 [ VRサウンドパッド ] 原作者授权。
- 如果需要缩短对 ToN 游戏状态的响应时间，可以调整 ToNSaveManager 设置中的 **常规-设置更新速率**，将更新速率设置为 100ms（默认为 1000ms，根据实际情况调整）。


## 快速开始

视频使用教程: https://www.bilibili.com/video/BV1k81VYfETR

1. 下载 [最新正式 Release](https://github.com/ccvrc/DG-LAB-VRCOSC/releases/latest) 中的 `DG-LAB-VRCOSC.zip`，解压后运行。
2. 点击主界面的 `启动` 来生成二维码，然后使用 DG-LAB APP 连接 DG-LAB 3.0 主机，点击 APP 中的 `SOCKET控制` 然后扫描此处二维码连接设备。
3. 在 VRChat 中开启 OSC。网络配置默认启用「自动发现 VRChat（OSCQuery）」：程序自动分配接收端口并发现本机 VRChat 的发送目标，无需填写 OSC 端口或设置 `--osc` 启动参数。程序和 VRChat 的启动顺序不限。
4. 如果遇到问题，可以查看网络配置中的 OSC 状态和日志。网卡及 WebSocket 端口用于 DG-LAB APP 扫码连接，与 VRChat 的自动 OSC 端口独立。

> 注意：你需要修改你使用的模型，才能让此程序与游戏中的 avatar 联动。
> ToN 游戏支持不需要修改模型，只需按上面的说明启用 ToNSaveManager 的 WebSocket API 接口即可。

### 更新通道

程序直接使用 GitHub 官方 API 和下载地址，不需要配置更新服务器。在设置中选择更新通道，默认使用「正式 Release」：

- **正式 Release（默认）**：只检查最新正式发布版本，忽略预发布构建。
- **Actions 最新构建（可选）**：检查 `master` 分支通过测试并完成打包的最新自动构建，适合提前使用尚未发布的修改。失败的构建、PR 和其他分支不会进入这个通道。

检查到更新后，由用户确认下载并安装。选择不同通道后可以手动检查更新；切回正式 Release 时，可安装相同基础版本或更高版本的正式包，不会自动降级到更早版本。安装会替换程序文件并保留同目录的用户配置。源码运行时，更新按钮打开相应 GitHub 发布页面。

Actions 构建会同时保存在 [工作流 Artifacts](https://github.com/ccvrc/DG-LAB-VRCOSC/actions/workflows/build-python-app.yml) 和带有 `build-` 标签的 [GitHub 预发布页面](https://github.com/ccvrc/DG-LAB-VRCOSC/releases)。程序下载的是同一次构建的公开 Release 附件，因此无需登录 GitHub 或填写访问令牌。网络需要能够访问 GitHub；访问失败时会显示错误，不会切换到第三方镜像。

### OSC 自动发现

- 自动模式显示程序实际监听的 UDP/HTTP 端口和已发现的 VRChat OSC 目标。VRChat 重启或改变端口后，程序会重新发现；等待发现不代表 DG-LAB APP 已连接。
- 发现通过本机 mDNS/OSCQuery 完成，并在 mDNS 无结果时使用 VRChat 本地日志作为候选。仅连接当前电脑上的 VRChat。
- 若自动服务启动失败，界面会显示错误并允许重试。需要兼容旧的固定端口配置时，可取消自动发现，手动设置接收端口；此时 VRChat 的输出端口应与该端口一致，程序向 VRChat 的发送目标为 `127.0.0.1:9000`。
- 参考 [VRCFaceTracking 的 OSCQuery/mDNS 实现](https://github.com/benaclejames/VRCFaceTracking/blob/6432e6a8d85fa7ec5115fc725c6abcb6dbdd4f35/VRCFaceTracking.Core/Services/OscQueryService.cs)与 [VRChat 官方 OSCQuery 文档](https://github.com/vrchat-community/osc/wiki/OSCQuery)。接收订阅保留 `/avatar` 子树；发送端口读取 VRChat 的 `HOST_INFO.OSC_PORT`，缺省时按协议采用 HTTP 服务端口。

## 问题反馈

如果在使用过程中遇到问题，欢迎在 [Issues](https://github.com/ccvrc/DG-LAB-VRCOSC/issues) 中提出。

访问[问题收集表](https://qiz80xlgzfj.feishu.cn/base/Db7KbBBmfaQmoXsk2BGcBddrnoc?table=tbl2hzoJWjaUkyyT&view=vewMnpNgGD)以查看当前收集到的BUG。

可以加入VRChat的游戏内群组 [DG-LAB-VRCOSC](https://vrc.group/CCVRC.1997) 来接收软件的更新动态。

## 注意事项

 1. 本程序及开发者不对使用该本程序产生的**任何后果**负责，使用程序则视为同意本条款。
 2. 请遵循 DG-LAB APP 中的说明，以安全的方式使用设备，使用此程序前请根据个人情况设置合理的强度上限。
 3. 本程序大部分代码使用 LLM 生成，未经过充分的测试！使用时请注意风险！
 
## 界面说明

> 以下是 v0.1 版本程序的界面

程序界面：
![DG-LAB-VRCOSC-MainUI-CN.png](docs%2Fassets%2FDG-LAB-VRCOSC-MainUI-CN.png)

SoundPad 控制面板界面：
![DG-LAB-VRCOSC-SoundPad-CN.png](docs%2Fassets%2FDG-LAB-VRCOSC-SoundPad-CN.png)

VRChat 游戏内轮盘菜单：
![DG-LAB-VRCOSC-VRChatMenu-CN.png](docs%2Fassets%2FDG-LAB-VRCOSC-VRChatMenu-CN.png)

## About

这个程序一开始只是为了做下面图片中的事情（画的好棒），后来想更完善些就加上了 UI 和 ToN 游戏的支持。

<div style="display: flex; align-items: center;">
    <img src="docs/images/dg-lab-start.png" alt="dg-lab-start" style="height: 450px; margin-right: 10px;">
    <img src="docs/images/misaka-h.png" alt="misaka-h" style="height: 450px;">
</div>
Artworks by Wanlin

## 编译与构建

### 环境准备
```bash
# 1. 安装 Python 3.12
# 下载并安装 Python 3.12: https://www.python.org/downloads/

# 2. 安装项目依赖
pip install -r requirements.txt pytest "pyinstaller>=6,<7"
```

### 本地回归测试

```bash
pip install pytest
python -m pytest -q
```

测试使用无窗口 Qt 控件和本机模拟的 HTTP/UDP 服务，覆盖参数编辑、日志线程、OSC 回调、自动发现、晚启动、端口变化、服务清理和更新通道。测试不广播 mDNS，不连接真实 VRChat 或 DG-LAB 设备；真实游戏发现、防火墙和设备效果仍需在实际环境验证。

### 构建步骤
```bash
# 3. 生成版本文件
./generate_version.ps1

# 4. 构建可执行文件
python scripts/build.py --clean

# 5. 验证打包程序可初始化（不开启 OSC 或设备连接）
python scripts/smoke_test_build.py dist/DG-LAB-VRCOSC.exe
```

版本生成脚本同时生成 `src/build-info.json`，记录版本、更新通道、提交和 Actions 运行编号。PyInstaller 会把它以及更新安装脚本内嵌到 EXE。自行构建时先运行版本生成脚本；生成的 JSON 不提交到 Git。构建脚本会隔离 Windows DLL 搜索路径，避免误打包其他工具的 ICU/OpenSSL 库。发布前还会在临时目录运行打包程序，确认界面初始化成功。

## 构建发布版本

### 创建发布标签

先将 `src/version.py` 设置为待发布版本并提交到 `master`。下例使用 `v0.4.10`；标签必须与源码版本一致。

```bash
# 1. 创建版本标签 (格式: vMAJOR.MINOR.PATCH)
git tag v0.4.10

# 2. 推送标签到远程仓库
git push origin v0.4.10
```

推送正式版本标签后，GitHub Actions 自动运行测试、构建 Windows EXE，并创建正式 Release，上传 `DG-LAB-VRCOSC.zip` 和 `build-info.json`。ZIP 根目录包含 EXE 和同一份构建信息，可直接解压运行。

每次推送 `master` 也会自动测试、打包并发布独立的预发布版本，标签格式为 `build-运行ID-重试次数`，版本号格式为 `v0.4.10.dev构建编号`。它们不会占用正式 Release 的 Latest 标记，也不会覆盖已有标签或包。其他分支、PR 和手动运行只上传 Artifact。发布使用工作流自身的 `GITHUB_TOKEN`，只有发布任务具有 `contents: write` 权限，不需要额外密钥或自建服务器。
