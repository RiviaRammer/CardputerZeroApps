"""Decode the CardputerZero evdev keyboard for the masked LCD password."""

import select

from evdev import InputDevice, ecodes, list_devices

LETTERS = {getattr(ecodes, "KEY_" + letter): letter.lower() for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
KEYS = (
    ecodes.KEY_1, ecodes.KEY_2, ecodes.KEY_3, ecodes.KEY_4, ecodes.KEY_5, ecodes.KEY_6,
    ecodes.KEY_7, ecodes.KEY_8, ecodes.KEY_9, ecodes.KEY_0, ecodes.KEY_MINUS, ecodes.KEY_EQUAL,
    ecodes.KEY_LEFTBRACE, ecodes.KEY_RIGHTBRACE, ecodes.KEY_BACKSLASH, ecodes.KEY_SEMICOLON,
    ecodes.KEY_APOSTROPHE, ecodes.KEY_GRAVE, ecodes.KEY_COMMA, ecodes.KEY_DOT, ecodes.KEY_SLASH,
    ecodes.KEY_SPACE,
)
SYMBOLS = dict(zip(KEYS, zip("1234567890-=[]\\;'\x60,./ ", "!@#$%^&*()_+{}|:\"~<>? ")))
SHIFT = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}
CONTROL = {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL}
ENTER = {ecodes.KEY_ENTER, ecodes.KEY_KPENTER}


class Keyboard:
    def __init__(self, stack):
        self.devices = []
        self.held = set()
        self.caps = False
        for path in list_devices():
            try:
                device = InputDevice(path)
            except OSError:
                continue
            try:
                keys = device.capabilities().get(ecodes.EV_KEY, [])
            except OSError:
                device.close()
                continue
            if ecodes.KEY_ESC in keys and ecodes.KEY_A in keys:
                stack.callback(device.close)
                self.devices.append(device)
            else:
                device.close()
        if not self.devices:
            raise OSError("Cannot read the CardputerZero keyboard")

    def events(self):
        if not self.devices:
            raise OSError("CardputerZero keyboard was disconnected")
        ready, _, _ = select.select(self.devices, [], [], 0.2)
        for device in ready:
            try:
                for event in device.read():
                    if event.type != ecodes.EV_KEY:
                        continue
                    key, value = event.code, event.value
                    if key in SHIFT | CONTROL:
                        if value:
                            self.held.add(key)
                        else:
                            self.held.discard(key)
                    elif key == ecodes.KEY_CAPSLOCK and value == 1:
                        self.caps = not self.caps
                    elif value == 1 or (key == ecodes.KEY_BACKSPACE and value == 2):
                        yield key
            except BlockingIOError:
                pass
            except OSError:
                self.devices.remove(device)

    def character(self, key):
        if self.held & CONTROL:
            return ""
        shift = bool(self.held & SHIFT)
        if key in LETTERS:
            letter = LETTERS[key]
            return letter.upper() if shift != self.caps else letter
        return SYMBOLS.get(key, ("", ""))[shift]
