"""Fixed destinations and media formats shared by the web app and writer."""

from dataclasses import dataclass
from pathlib import Path

BOOT = Path("/boot/firmware")
APP = Path("/usr/share/APPLaunch/share")
DROP_IN = Path("/etc/systemd/system/fb_load.service.d/90-custom-splash.conf")
LINUX_SPLASH = BOOT / "splash.bmp"
LEGACY_LINUX_SPLASH = BOOT / "custom-splash.bmp"
DROP_IN_CONTENT = f"[Service]\nExecStart=\nExecStart=/usr/sbin/fb_load --keep-open {LINUX_SPLASH}\n".encode()
LEGACY_DROP_IN_CONTENT = DROP_IN_CONTENT.replace(str(LINUX_SPLASH).encode(), str(LEGACY_LINUX_SPLASH).encode())
WRITER = "/usr/lib/wallpaper/write-asset"
WRITER_READY = b"ready\n"
AUDIO_RATE = 44100
AUDIO_SECONDS = 30
PCM_LIMIT = AUDIO_RATE * 2 * 2 * AUDIO_SECONDS


@dataclass(frozen=True)
class Asset:
    title: str
    lcd_title: str
    destination: Path
    size: tuple[int, int] | None = (320, 170)

    @property
    def payload_size(self):
        width, height = self.size
        return width * height * 3


ASSETS = {
    "firmware": Asset("开机画面 · 第一阶段", "1. Firmware splash", BOOT / "CardputerZero_splash_custom.bin"),
    "linux": Asset("开机画面 · 第二阶段", "2. Linux splash", LINUX_SPLASH),
    "launcher": Asset("开机画面 · 第三阶段", "3. APPLaunch logo", APP / "images/logo_lcd.png"),
    "sound": Asset("开机音频", "Boot sound", APP / "audio/Ding2.wav", size=None),
    "lockscreen": Asset("锁屏画面", "Lock screen", APP / "images/lockscreen.png", size=(320, 150)),
}


def valid_pcm(data):
    return 0 < len(data) <= PCM_LIMIT and len(data) % 4 == 0


def write_wav(output, pcm):
    import wave

    with wave.open(output, "wb") as wav:
        wav.setparams((2, 2, AUDIO_RATE, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
