# CardputerSDR

Cardputer Zero + Airspy Mini 的频谱 / 瀑布图应用。支持机身 LCD 或手机、电脑浏览器显示。仅接收；Bias-T 关闭。

## 安装、启动

```sh
sudo apt install ./cardputersdr_0.1.3_arm64.deb
```

从 APPLaunch 打开 **CardputerSDR**。默认 LCD 模式，按 **W** 切换到 Web，然后访问 LCD 显示的地址（默认端口 **1420**）。按 **L** 回到 LCD，**Esc** 退出并释放接收机与 Web 端口。不注册开机服务。

| 按键 | 操作 |
|---|---|
| W / L | Web / LCD 模式 |
| Z / C 或左 / 右 | 中心频率减 / 加一个步进 |
| S | 1 kHz / 10 kHz / 100 kHz / 1 MHz 步进 |
| 数字、`.`、Enter | 输入 MHz 并调谐 |
| `-` / `=` | 增益减 / 加；F / X 分别为加 / 减 |
| R | 重新打开 Airspy，适用于拔插后的恢复 |
| Esc | 取消频率输入；否则退出 |

Web 页面提供频率、采样率、线性增益、底噪范围、画面暂停和模式切换。所有客户端共享同一调谐状态。默认监听局域网所有接口，没有登录口令；同网络可访问者可操作接收机，应用退出即关闭。限制四个 WebSocket 客户端，控制请求检查 Origin。

无 LCD / 键盘运行：

```sh
cardputersdr --headless
cardputersdr --headless --bind 127.0.0.1 --port 1420
cardputersdr --mode web --frequency 145500000 --gain 8
```

设备不存在时显示错误，不会自动用假数据替代。重新连接后按 R 或网页“重连 Airspy”。默认选择设备报告的最低采样率。采样率也可通过 `--rate 6000000` 设置。

## 运算、带宽和限制

- LCD：4096 点 Hann 窗 FFT，目标 8 fps，320×170 频谱与瀑布图。
- Web：每个浏览器独立选择 FFT Size（1024、2048、4096、8192、16384，默认 4096）。Zero 保留最新的连续 `int16` I/Q 采样块，并按客户端所选点数发送，目标最高 15 fps。Web Worker 在客户端做 FFT，Canvas 画频谱与瀑布图。
- 每帧 32 字节头加每点 4 字节。按 15 fps 计算，4096 点约 **1.97 Mbit/s**，16384 点约 **7.87 Mbit/s**。实际帧率受 USB 回调周期和客户端速度影响。
- 这是**采样块快照**，帧与帧之间有时间空隙，保留瞬时全带宽。不是无间断 I/Q 流，不用于完整录音、解调或保证捕获短脉冲。3 MS/s 的连续 int16 I/Q 本身需要 96 Mbit/s，另加协议开销，会超过当前 USB 百兆网口的实用吞吐。
- libairspy 的 USB 接收和 ADC→I/Q 转换仍在 Zero 上执行。Web 卸载 FFT / 图形计算，不等于 Zero 完全不做 DSP。默认低采样率也降低接收开销。
- FFT 使用连续复数样本，窗口按相干增益归一化。频点间隔等于采样率除以 FFT 点数。显示相对 dBFS/bin，未经天线、增益和频率响应校准，不能当作绝对 dBm。频率轴包含边缘滤波区域。
- 每客户端处理完一帧才请求下一帧；服务器不累积历史数据。USB 丢样数独立显示，正常跳过快照之间的样本不计为 USB 丢样。退出、调谐和设备断流会清除旧数据。
- 当前版本不含音频解调、录制、扫描、发射或持久化设置。

## 开发

```sh
sudo apt install python3-aiohttp python3-numpy python3-pil python3-evdev libairspy0 airspy nodejs
python3 -B -m unittest discover -s tests -v
python3 tools/generate_assets.py
sh package.sh
```

包纯 Python，无需交叉编译。`package.sh` 在 Debian / WSL 或设备上生成 arm64 `.deb`。本地明确模拟模式：`python -m cardputersdr.app --demo --headless`，页面始终标示 DEMO。`screenshots/*demo.png` 是模拟界面，不是真机射频证据。

安装包和预览图分别生成到 `dist/` 和 `screenshots/`。设备配置、日志和采样数据应保存在本地，可使用项目中由 Git 忽略的 `.local/` 目录。

接收 API 依据上游 [airspy.h](https://github.com/airspy/airspyone_host/blob/master/libairspy/src/airspy.h) 和 [airspy_rx](https://github.com/airspy/airspyone_host/blob/master/airspy-tools/src/airspy_rx.c)。系统 libairspy 动态链接，不随源码复制。应用许可证为 MIT。

## 二进制 WebSocket 协议

访问 `/iq`，发送文本 `next` 请求默认 4096 点，或发送 `next <点数>` 选择上述任一 FFT Size。模式不是 Web 或没有新数据时返回 JSON 状态。有数据时返回 little-endian 二进制：

| 偏移 | 类型 | 内容 |
|---|---|---|
| 0 | char[4] | `ZIQ1` |
| 4 | uint32 | sequence（允许跳号） |
| 8 | uint32 | 中心频率 Hz |
| 12 | uint32 | 复数采样率 samples/s |
| 16 | uint32 | 本帧样本数 |
| 20 | uint32 | 线性增益 0–21 |
| 24 | uint64 | 当前流累计 USB 丢样 |
| 32 | int16[2 × 样本数] | I0,Q0,I1,Q1… |

`GET /api/status` 查询状态；`POST /api/control` 接收 JSON 对象，可包含 `frequency`、`rate`、`gain`、`mode`。空对象重试失效流。状态和数据只在应用运行时提供。
