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
- 如果需要缩短 ToN 游戏的响应时间，可以调整 ToNSaveManager 设置中的 **常规-设置更新速率**，将更新速率设置为 100ms（默认为 1000ms，根据实际情况调整）。

## 快速开始

1. 下载 [release](https://github.com/ccvrc/DG-LAB-VRCOSC/releases) 中最新版本的 `DG-LAB-VRCOSC.zip`，解压后运行
2. 点击主界面的 `启动` 来生成二维码，然后使用 DG-LAB APP 连接 DG-LAB 3.0 主机，点击 APP 中的 `SOCKET控制` 然后扫描此处二维码连接设备。
3. 如果遇到问题，可以通过日志排查。建议检查网卡和端口是否设置正确，修改后再次尝试启动。

> 模型修改文档（编写中|WIP)

## 应用界面

![DG-LAB-VRCOSC-MainUI-CN.png](docs%2Fassets%2FDG-LAB-VRCOSC-MainUI-CN.png)

## About

这个程序大部分代码使用 ChatGPT 生成，一开始只是为了做下面图片中的事情（画的好棒），后来想更完善些就加上了 UI。

<div style="display: flex; align-items: center;">
    <img src="docs/images/dg-lab-start.png" alt="dg-lab-start" style="height: 450px; margin-right: 10px;">
    <img src="docs/images/misaka-h.png" alt="misaka-h" style="height: 450px;">
</div>
Artworks by Wanlin