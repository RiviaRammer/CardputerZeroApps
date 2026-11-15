# Wallpaper

从 APPLaunch 打开 **Wallpaper**。LCD 显示局域网 IP 和端口。用同一网络上的电脑或手机打开这个地址。

1. 在对应卡片选择文件。「生成预览」和「查看当前」都用该卡片的同一预览区域。
2. 备份需要保留的资源，再点单项的「写入此项」，或者点页首的「写入所有已选文件」。批量操作会处理所有已选文件。
3. 在 LCD 上核对待写入项目，按 **Enter** 继续。系统 sudo 需要认证时，LCD 会显示当前账户，在板子的键盘输入该账户的密码并按 **Enter**。单项或整批都只发起一次 sudo 操作。输入支持英文字母、数字和常见符号，Backspace 可删字符，Ctrl+U 可清空。按 **Esc** 可取消确认或密码输入。
4. 网页显示写入结果后，重启检查开机资源。空闲时按 **Esc** 可关闭网页并回到 APPLaunch。

网页使用局域网 HTTP。同一网络上的设备能查看已有资源和生成预览。密码只经本地 Unix 套接字交给系统 sudo，不进入网页。默认端口为 8765，占用时 LCD 显示自动选取的端口。

## 打包与安装

在 Linux、WSL 或板上，从完整工作区进入本目录后构建。

```sh
sh package.sh
```

打包会直接带入工作区里的 `初始化配置/build_splash.py`。如果单独检出了 CardputerZeroApps，把原转换脚本下载到本地并用 `SPLASH_BUILDER=/绝对路径/build_splash.py sh package.sh` 指定。构建环境需要 `dpkg-deb`。

把 `dist/wallpaper_0.4.1_arm64.deb` 复制到板上，执行：

```sh
sudo apt install ./wallpaper_0.4.1_arm64.deb
```

APT 会安装 Pillow、evdev、FFmpeg 等依赖。应用以 APPLaunch 用户运行，需要该用户能读写 framebuffer 并读取键盘。软件包不修改 sudoers、不添加用户组，也不安装 root 服务。

系统 sudo 按设备现有权限决定是否需要认证。需要有 sudo 权限的账户操作。写入程序接收定长 RGB 像素或有限长度的 PCM，并自行生成资源写到固定目标。若试装过 0.1.0，可先运行 `sudo apt purge cardputer-boot-assets` 清除旧包的 sudoers 配置，再装新版。

## 图片和音频

- 启动图输出为 **320 × 170（32:17）**，锁屏图为 **320 × 150（32:15）**，顶部保留 APPLaunch 自带的 20 像素状态栏。图片支持 PNG、JPEG、WebP 和 BMP。应用按 EXIF 方向转正，等比缩放并居中裁切，透明处显示黑底。想保留边缘文字或精确控制构图，可以直接上传目标尺寸的图。
- 启动音频支持 WAV、MP3、FLAC 和 Ogg，最长 30 秒。输出为 **44.1 kHz、16 位 PCM、立体声 WAV**。
- 单文件最大 16 MiB，输入图片最大 1600 万像素。每次可预览一项，预览 10 分钟内有效。

## 当前文件与写入路径

「查看当前」读取板上已有的资源。软件会覆盖下列文件。写入前请自行备份到另一台设备。

| 资源 | 文件 |
| --- | --- |
| 第一阶段 · 固件 | `/boot/firmware/CardputerZero_splash_custom.bin` |
| 第二阶段 · Linux | `/boot/firmware/splash.bmp` |
| 第三阶段 · APPLaunch | `/usr/share/APPLaunch/share/images/logo_lcd.png` |
| 开机提示音 | `/usr/share/APPLaunch/share/audio/Ding2.wav` |
| 锁屏画面 | `/usr/share/APPLaunch/share/images/lockscreen.png` |

第一阶段以出厂 `config.txt` 中的 `splash_screen=CardputerZero_splash_custom.bin` 为准。第二阶段保留 BMP 的原文件名。如果 `fb_load` 已经读取 `splash.bmp`，只覆盖文件。出厂服务默认读取内嵌图，这时 Wallpaper 会写入 `/etc/systemd/system/fb_load.service.d/90-custom-splash.conf`，让它改读 `splash.bmp`。旧版 Wallpaper 留下的同名配置也会改用这一文件名。

要恢复第二阶段的内嵌图，可移除上述 drop-in，运行 `sudo systemctl daemon-reload` 后重启。要还原磁盘上的图或音频，请使用自己的备份。打包脚本输出 `wallpaper_0.4.1_arm64.deb`，Debian 包名为 `wallpaper`。它与早期的 `cardputer-boot-assets` 同属一个应用，两者不能同时安装。商店会把新包名识别为新的提交。

应用在写入时保留当前的 LCD 会话。卸载本应用不会还原资源或移除第二阶段的 drop-in。系统更新 `applaunch` 包后可复查第三阶段、锁屏图和提示音是否已被包内资源覆盖。
