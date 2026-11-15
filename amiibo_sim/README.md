# amiibo_sim

Cardputer Zero 上的 Amiibo LCD 应用，使用 Cap CC1101 上的 ST25R3916。界面采用 CardputerZero-Template 的 LVGL 9.5 framebuffer、Linux evdev 键盘和 APPLaunch 入口。 [CardputerZero Template](https://github.com/CardputerZero/Template) · [Cap NFC 配置](https://github.com/CardputerZero/Cap-CC1101-NFC#hardware)

## 安装和导入

在 Zero 上安装 `amiibo-sim_0.1.9-1_arm64.deb`，然后从 APPLaunch 打开 **amiibo_sim**。

```sh
sudo apt install ./amiibo-sim_0.1.9-1_arm64.deb
```

首次打开时，应用会自动创建当前用户的 `~/amiibo_sim/`。把你自己的 **540 字节原始 NTAG215 备份**放在这里，或按游戏系列放进多级子目录。屏幕只显示当前一层，文件夹排在备份前面。支持中文文件夹和文件名，以及大小写混用的 `.bin` 扩展名。长名称在选中时滚动。中文显示使用安装时随包解析的 Debian 字体依赖。

如果要刷新 UID，把你自己的 **160 字节**密钥放在 `~/amiibo_sim/` 根目录，文件名用 `key_retail.bin` 或 `retail.bin`。所有子目录共用这份密钥。两个文件都可读取且大小正确时，优先使用 `key_retail.bin`。安装包不包含任何 Amiibo 备份、零售密钥或生成的 UID 池。

- **F / X** 或方向键移动，**Enter** 打开选中的文件夹或启动选中的备份。
- 开始模拟时隐藏左上角路径并暂停文件名滚动。停止模拟后恢复路径和滚动。
- **T** 重新扫描当前目录，**H** 重试 Cap 初始化。
- 浏览时按 **Esc** 返回上一级，光标停在刚退出的文件夹。在根目录按 **Esc** 退出。
- 右上角显示密钥是否找到、能否读取且大小正确。选择备份时验证密钥与备份能否签名。可签名时，应用为本次选中的备份生成 256 个预签名变体，另外可随时恢复原始 UID。每次重新选卡时重新生成。
- 模拟时按 **R** 随机换一个 UID，**O** 恢复原始备份。移开 Switch 手柄后才会切换。**Esc** 停止模拟并回到当前目录。
- 没有密钥时可以用原始 UID。签名失败时，应用显示错误并继续使用原始 UID。
- 芯片报告读页和认证回复均已发送后，LCD 显示 `NFC replies sent - check Switch`。以 Switch 上的游戏提示为准。读取未完成则显示 `Read incomplete - try again`。只选卡后立即离开的探测不会更新结果。

安装新包时，APT 会替换旧的 `zero-amiibo` 安装包和启动器入口。升级后，把旧 `~/zero_amiibo/` 或 `~/amiibo/` 里的备份和密钥复制到 `~/amiibo_sim/`，再按 **T** 扫描。

## Cap 供电和 SPI

APPLaunch 以当前用户运行应用。有权限时会自动开启并核对 EXT 5V、拉高 Cap 的 POWER_EN，然后重试芯片探测约 3 秒。系统只允许管理员动态加载 overlay，因此首次使用时若缺少 `/dev/spidev0.2`，请在 Zero 终端手动运行一次：

```sh
sudo /usr/lib/amiibo_sim/cap-setup
```

应用在空闲时会自动复查，保持界面打开即可，也可以按 **H** 立即重试。**T** 会刷新文件并在 Cap 尚未就绪时重新检查。以管理员运行的脚本会将 NFC SPI 节点设为 `root:spi 0660`，让 `spi` 组的用户可以访问。这个命令也能处理屏幕上的 `Enable POWER_EN` 提示。它保留 Cap 供电，因为 Cap 和 LCD 共用 SPI。 [官方 Cap NFC 硬件说明](https://github.com/CardputerZero/Cap-CC1101-NFC#hardware)

- **Set EXT D+/D- to GPIO** 时，去 `Settings → ExtPort` 把 EXT D+/D− 切回 GPIO。
- **SPI overlay missing** 时，更新 Zero 官方系统以获取 Cap 的 overlay。
- 如果手动运行后仍然缺少 SPI 节点，在 `/boot/firmware/config.txt` 的 `[all]` 段加入一次 `dtoverlay=spi0-spidev2-gpio22-overlay`，重启后再打开应用。
- **EXT 5V failed** 时，在 `Settings → ExtPort` 开启 EXT 5V。 [Zero 操作说明](https://docs.m5stack.com/en/compute/cardputerzero/usage)

SPI 是 Zero 与 ST25R3916 之间的有线通信总线。Linux 的 `/dev/spidev0.2` 表示 SPI 总线 0 的片选 2。Cap 用 GPIO22 做片选，用 GPIO23 通知 Zero 有 NFC 事件。与 Switch 的 13.56 MHz 无线通信由 ST25R3916 完成。 [官方 Cap NFC 硬件说明](https://github.com/CardputerZero/Cap-CC1101-NFC#hardware)

`probe` 是原命令行工具的子命令。它通过 SPI 读取 ST25R3916 的芯片 ID。`--spi DEVICE` 才是可选参数，用来指定设备节点。`probe` 成功表示芯片可以通过 SPI 访问，完整的 NFC 读页和认证要在 `emulate` 时用 Switch 验证。

## 构建 LCD 版

在 Linux 或 WSL 中使用 CMake、Ninja 和 AArch64 工具链。可以用 `-DLVGL_SOURCE_DIR=/path/to/lvgl` 指定 Template 已缓存的 LVGL 9.5 源码。

```sh
cmake -S . -B build/lcd-arm64 -G Ninja \
  -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
  -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++
cmake --build build/lcd-arm64
sh package.sh
```

## 原命令行版

在 Cardputer Zero 的板载 CLI 或 SSH 终端中模拟 Amiibo。程序使用 Cap CC1101 上的 ST25R3916 做 13.56 MHz NFC-A 被动卡模拟，通过 Linux SPI0 CS2 和 GPIO23 IRQ 收发 NTAG215 命令。启动时加载 540 字节的原始备份并校验 UID 的两个 BCC 字节。 [Cap CC1101](https://docs.m5stack.com/zh_CN/cap/Cap_CC1101) · [Zero 的 Cap NFC 板级配置](https://github.com/CardputerZero/Cap-CC1101-NFC#hardware)

### 在 Zero 上运行

将 `build/amiibo-zero-arm64` 和自己的 `my-amiibo.bin` 传到设备的同一目录。使用板上本机编译产物时，将以下命令里的 `amiibo-zero-arm64` 换成 `build/amiibo-zero`。

1. 在 `Settings > ExtPort` 开启 EXT 5V，保持 EXT 的 D+/D− 处于默认 GPIO 模式，退出正在使用 Cap NFC 的其他应用。若 EXT 5V 已开启但探测不到芯片，可在 Zero 上运行 `sudo pinctrl set 26 op dh`，将 Cap 的 POWER_EN 拉高。Cap 与 LCD 共用 SPI，模拟结束后保留模块供电。 [Zero 操作指南](https://docs.m5stack.com/en/compute/cardputerzero/usage) · [Cap NFC 板级配置](https://github.com/CardputerZero/Cap-CC1101-NFC#hardware)
2. 确认 `/dev/spidev0.2` 存在。若缺失，在官方系统的 `/boot/firmware/config.txt` 的 `[all]` 段加入一次 `dtoverlay=spi0-spidev2-gpio22-overlay` 后重启。该 overlay 由 Zero BSP 提供，使用 GPIO22 作为 NFC 片选。 [官方应用的 overlay 说明](https://github.com/CardputerZero/Cap-CC1101-NFC#hardware)
3. 在 CLI 或 SSH 中运行以下命令。出现 `Emulating` 后，将 Cap 的 NFC 区域贴近 Switch 手柄的 Amiibo 感应区。移开手柄后查看会话统计，`auth` 大于零表示读卡器发来的 Amiibo 密码通过校验。按 Ctrl+C 停止。

   ```sh
   chmod +x amiibo-zero-arm64
   ./amiibo-zero-arm64 check my-amiibo.bin
   sudo ./amiibo-zero-arm64 probe
   sudo ./amiibo-zero-arm64 emulate my-amiibo.bin
   ```

`check` 校验文件尺寸与 UID/BCC。`probe` 读取 ST25R3916 身份寄存器。默认使用 `/dev/spidev0.2` 和 `/dev/gpiochip0`。如果系统的 BCM GPIO 控制器编号不同，运行 `gpioinfo` 确认 GPIO23 所属控制器，再向 `emulate` 传入 `--gpiochip /dev/gpiochipN`。具备这两个设备节点的访问权限时，可以直接运行命令。

如果 Switch 拒绝识别，在 `emulate` 命令末尾加 `--trace`。每次移开手柄后，终端会列出命令码、读页范围、回复长度和发送状态。跟踪日志省略 UID、备份内容和认证密码。`Scan result: success` 表示芯片报告读页和认证回复均已发送。

### 切换 UID

可选。使用本机的 Python 3.10+ 和 `cryptography`，从本目录运行下面的命令。同目录的 `generate_uid_pool.py` 提供签名实现。用同一份原始备份生成 540 字节变体，再把原始备份和池传到 Zero。命令行模拟只加载备份和变体。

```sh
python3 -B make_uid_pool.py --dump my-amiibo.bin --key key_retail.bin --output uid-pool.bin
sudo ./amiibo-zero-arm64 emulate my-amiibo.bin --uid-pool uid-pool.bin
```

移开读卡器后，输入 `r` 加回车，随机选择另一个 UID 变体。输入 `o` 加回车，恢复原始备份并丢弃当前内存里的游戏写入。多个变体共用一个池文件，最多 256 个。省略 `--uid-pool` 时，直接用原始 UID 模拟。

### 模拟范围

- 支持 `READ`、`FAST_READ`、`GET_VERSION`、`PWD_AUTH`、`READ_SIG`、`READ_CNT`、`CHECK_TEARING_EVENT` 和 `HLTA`。默认签名占位、计数与防撕裂响应沿用参考固件。`--signature original.sig` 可为原始 UID 提供匹配的 32 字节签名。
- `WRITE` 按 ADV 和 nRF52832 版的行为更新用户页 4–129，返回 4 位 ACK/NAK。写入保存在 RAM，恢复原始备份、切换变体或退出进程都会丢弃。
- Zero 版使用终端操作。板级驱动参考 M5Stack 的 NFC-A 被动目标实现及 ST 和 NXP 数据手册。 [M5Unit-NFC](https://github.com/m5stack/M5Unit-NFC) · [NTAG215 数据手册](https://www.nxp.com/docs/en/data-sheet/NTAG213_215_216.pdf) · [ST25R3916 数据手册](https://www.st.com/resource/en/datasheet/st25r3916.pdf)

### 从源码构建

在 Linux 或 WSL 中进入本目录。依赖为 C11 编译器、Make 和 Linux SPI/GPIO UAPI 头文件。交叉构建另需 `aarch64-linux-gnu-gcc`。

```sh
make test      # 在构建机上运行 NTAG215 和 NFC 中断回归测试
make           # 本机编译到 build/amiibo-zero
make arm64     # 交叉编译到 build/amiibo-zero-arm64
```

## 许可证

项目采用 [MIT 许可证](LICENSE)。依赖及参考实现的署名见 [第三方声明](THIRD_PARTY_NOTICES.md)。
