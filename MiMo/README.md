# MiMo Talk

CardputerZero / Zero Lite 的 ARM64 Linux 语音对话应用。沿用本仓库 amiibo_sim 的 LVGL 9.5、320 × 170 framebuffer、evdev 键盘、中文字体和 APPLaunch 安装方式。

## 安装与使用

如果装的是最初的 `mimo` 包，先用 `sudo apt remove mimo` 卸载它。已安装 `mimo-talk` 时可直接升级。

```sh
sudo apt install ./mimo-talk_0.2.2_arm64.deb
```

在系统 Settings 中连接 Wi-Fi、调整扬声器音量，然后从 APPLaunch 打开 **MiMo Talk**。应用以当前用户身份运行。APT 会安装 Python requests、ALSA 工具及 PulseAudio 插件、CA 证书和中文字体。

首次启动会在当前用户的家目录创建 **`~/mimo_talk/config.json`**。如果新目录还没有配置，应用会从旧的 `~/MiMo/config.json` 复制有效设置。迁移只读取旧配置文件。

1. 按 **S** 进入设置，**F/X** 或上下方向键选择字段，**Enter** 编辑。LCD 上用 **↵** 表示 Enter。
2. 用实体键盘输入 API Key。支持 Shift、Caps Lock、数字和符号；API Key 始终掩码显示。**Ctrl+U** 清空，Backspace 删除，左右方向键移动光标，**Enter** 保存，**Esc** 放弃编辑。再次 Esc 返回聊天。LCD 输入为英文/数字；中文音色和提示词可直接编辑 JSON，默认提示词已经是中文。
3. 按 **Enter** 开始录音，再按 **Enter** 结束并发送；默认最多录音 30 秒。没有后台监听或自动录音。
4. 屏幕逐步显示识别内容与回答；回答按句进入 TTS，收到 PCM 块即送到扬声器，聊天生成与播报并行。
5. **Esc** 取消当前录音/请求/播放；空闲时 Esc 退出。**C** 清空本次会话，**F/X** 滚动查看文本。下次按 Enter 可继续对话。

网络取消会立即停止音频并通知后台；如果连接正在等待服务器，需等待该请求超时返回（连接 8 秒、读取 12 秒）才会恢复空闲。取消期间不能启动另一轮录音。退出会停止后台和 ALSA 进程。

## 直接配置文件

编辑 `~/mimo_talk/config.json`，格式参见 [config.example.json](config.example.json)。保存后重新进入设置或开始下一次录音就会重新读取。

| 字段 | 默认值 / 作用 |
| --- | --- |
| `api_key` | 空；填入 MiMo 开放平台 API Key |
| `base_url` | `https://api.xiaomimimo.com/v1`；只接受 HTTPS，保留证书校验 |
| `chat_model` | `mimo-v2.6-flash` |
| `asr_model` | `mimo-v2.5-asr` |
| `tts_model` | `mimo-v2.5-tts`，用于预置音色 |
| `voice` | `mimo_default`；可用 `冰糖`、`茉莉`、`苏打`、`白桦`、`Mia`、`Chloe`、`Milo`、`Dean` |
| `capture_device` / `playback_device` | `default` 优先使用当前用户的 PipeWire/PulseAudio 会话，检测不到音频服务时使用 ALSA 默认设备。也可显式指定 `pulse` 或其他 ALSA PCM 名称 |
| `max_record_seconds` | 30，范围 1–60 秒 |
| `max_completion_tokens` | 512，范围 64–2048 |
| `history_turns` | 4，范围 0–10；0 不携带历史 |
| `system_prompt` | 中文简短口语回答提示词 |

配置原子写入，新建文件权限为 `0600`，新建目录为 `0700`。非法 JSON、无效字段和类型错误会显示在 LCD，原文件保留。修复后按 S 重新读取。编辑设置会清空对话历史。聊天历史和录音只存在内存。录音会发送到配置的 API 服务，API Key 保存在本地配置中。

## API 对齐与流式范围

以 **2026-11-14 查询的官方文档**为准，参考 MiMoKit 的 ASR → Chat → TTS 架构，自行实现 Zero 版。

- 三阶段均调用 `/v1/chat/completions`，使用 `api-key` 鉴权和 `stream: true`，解析 SSE `choices[0].delta`。
- 录音采用 16 kHz、16 位单声道 WAV，Base64 `input_audio` 交给 ASR。此接口是**录完上传、流式返回识别文本**，并非边录边传的 WebSocket 全双工语音接口。
- 文本模型使用 `max_completion_tokens`、`thinking.type: disabled`；只播报 `delta.content`。
- TTS 通过 `assistant` 消息提交待朗读文本，`audio.format: pcm16`；解码 `delta.audio.data`，按官方 24 kHz / PCM16LE / 单声道播放。
- 文本按中文/英文句末标点分段，过长段落在 160 字符处拆分；最多缓存 8 段，保持顺序并限制内存。
- 429/502/503/504 在响应流开始前最多重试两次，遵守数字秒数的 `Retry-After`（上限 60 秒），否则按 2、4 秒退避。已经开始的流不会自动重放，避免重复播报。账户共享限额可能被其他应用消耗。
- 使用开放平台按量 API Key。

参考：[MiMoKit](https://github.com/RiviaRammer/MiMoKit) · [ASR](https://mimo.mi.com/docs/zh-CN/api/audio/Speech-Recognition-post) · [Chat](https://mimo.mi.com/docs/zh-CN/api/chat/openai-api-post) · [TTS](https://mimo.mi.com/docs/zh-CN/api/audio/tts-post) · [TTS 流式 PCM 示例](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5) · [限流](https://mimo.mi.com/docs/zh-CN/api/guidance/rate-limit)

## 构建与验证

Linux/WSL 需要 CMake、Ninja、AArch64 交叉编译器和 dpkg-dev。与 amiibo_sim 相同，可以指定已经下载的 LVGL 9.5 源码；省略该参数时自动获取 v9.5.0。

```sh
cmake -S . -B build/arm64 -G Ninja \
  -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
  -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
  -DLVGL_SOURCE_DIR=/path/to/lvgl-9.5
cmake --build build/arm64 -j4
sh package.sh
python3 -B -m unittest discover -s tests -v
```

输出 `dist/mimo-talk_0.2.2_arm64.deb`。仓库本机缓存路径适用时，也可运行 `sh build-local.sh`。测试不访问外部 API，不需要 API Key。

## 真机验收与排查

- 若 LCD 显示录音失败，先在 **FactoryTest** 验证麦克风，再以启动 MiMo 的同一用户在 Zero 终端运行 `arecord -D pulse -f S16_LE -r 16000 -c 1 -d 3 /tmp/mimo-mic.wav`。如果命令失败，查看它输出的系统错误。若系统音频服务可用，但此前在 MiMo 手动指定了设备，可以把 `capture_device` 和 `playback_device` 改回 `default`。
- 在系统录音应用确认内置麦克风正常，检查系统音量，再测试中文问答、连续两轮历史、取消、断网和错误 Key。真实硬件的音频路由、键盘 Fn 映射和 API 账户权限必须在设备上验证。
- LCD 沿用 `LV_LINUX_FBDEV_DEVICE` / `APPLAUNCH_LINUX_FBDEV_DEVICE`，默认 `/dev/fb0`。先识别 tca8418 内置键盘，否则选择支持字母和 Enter 的 evdev 设备。
- HTTPS 失败时先检查 Wi-Fi、系统日期和 CA 证书。401 检查 Key，402 检查余额，429 稍后重试。
- 此项目未包含密钥，也不在构建/测试时调用付费 API。
