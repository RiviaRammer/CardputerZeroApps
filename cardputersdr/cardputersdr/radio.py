"""Airspy Mini/One capture. Only copy bounded, contiguous IQ windows.

libairspy still performs its USB/ADC-to-IQ conversion in both modes. FFT and
waterfall processing happen exclusively in the selected display's process.
"""
import ctypes as C
import ctypes.util
import math
import struct
import threading
import time
from . import __version__

N = 4096
WEB_FFT_SIZES = (1024, 2048, N, 8192, 16384)
WEB_CAPTURE_SIZE = WEB_FFT_SIZES[-1]
HEADER = struct.Struct('<4sIIIIIQ')  # magic, seq, frequency, IQ rate, N, gain, dropped


class Transfer(C.Structure):
    _fields_ = [('device', C.c_void_p), ('ctx', C.c_void_p), ('samples', C.c_void_p),
                ('sample_count', C.c_int), ('dropped_samples', C.c_uint64),
                ('sample_type', C.c_int)]


Callback = C.CFUNCTYPE(C.c_int, C.POINTER(Transfer))


def spectrum(payload):
    """Relative dBFS per FFT bin, Hann coherent-gain normalization."""
    import numpy as np
    iq = np.frombuffer(payload, dtype='<i2').astype(np.float32).reshape(-1, 2)
    signal = (iq[:, 0] + 1j * iq[:, 1]) / 32768.0
    window = np.hanning(len(signal))
    power = abs(np.fft.fftshift(np.fft.fft(signal * window))) / window.sum()
    return 20 * np.log10(np.maximum(power, 1e-7))


class Radio:
    def __init__(self, demo=False, frequency=100_000_000, gain=10, rate=None):
        self.demo = demo
        self.frequency, self.gain, self.rate = frequency, gain, rate
        self.rates = [3_000_000, 6_000_000]
        self.lock = threading.RLock()
        self.control_lock = threading.RLock()
        self.device = C.c_void_p()
        self.lib = None
        self.seq = self.dropped = 0
        self.latest = None
        self.last_frame = self.last_capture = 0.0
        self.error = ''
        self.mode = 'lcd'
        self.lcd_available = True
        self.running = False
        self.fps = 15
        self.worker = None
        self.stop_event = threading.Event()
        self.callback = Callback(self._receive)  # keep alive until airspy_close

    def _check(self, name, *args):
        result = getattr(self.lib, name)(*args)
        if result != 0:
            raise RuntimeError(f'{name}: {self.lib.airspy_error_name(result).decode()} ({result})')

    def open(self):
        with self.control_lock:
            self._open()

    def _open(self):
        if self.running:
            return
        self.stop_event.clear()
        self.error = ''
        try:
            if self.demo:
                self.rate = self.rate or min(self.rates)
            else:
                path = C.util.find_library('airspy')
                if not path:
                    raise RuntimeError('Install libairspy0; Airspy runtime not found')
                self.lib = C.CDLL(path)
                signatures = {
                    'airspy_open': [C.POINTER(C.c_void_p)],
                    'airspy_close': [C.c_void_p],
                    'airspy_get_samplerates': [C.c_void_p, C.POINTER(C.c_uint32), C.c_uint32],
                    'airspy_set_samplerate': [C.c_void_p, C.c_uint32],
                    'airspy_set_sample_type': [C.c_void_p, C.c_int],
                    'airspy_set_freq': [C.c_void_p, C.c_uint32],
                    'airspy_set_linearity_gain': [C.c_void_p, C.c_uint8],
                    'airspy_set_rf_bias': [C.c_void_p, C.c_uint8],
                    'airspy_start_rx': [C.c_void_p, Callback, C.c_void_p],
                    'airspy_stop_rx': [C.c_void_p],
                    'airspy_is_streaming': [C.c_void_p],
                }
                for name, args in signatures.items():
                    getattr(self.lib, name).argtypes = args
                    getattr(self.lib, name).restype = C.c_int
                self.lib.airspy_error_name.argtypes = [C.c_int]
                self.lib.airspy_error_name.restype = C.c_char_p
                self._check('airspy_open', C.byref(self.device))
                count = C.c_uint32()
                self._check('airspy_get_samplerates', self.device, C.byref(count), 0)
                if not 0 < count.value <= 64:
                    raise RuntimeError('Invalid Airspy sample rate list')
                rates = (C.c_uint32 * count.value)()
                self._check('airspy_get_samplerates', self.device, rates, count)
                self.rates = sorted(set(rates))
                self.rate = self.rate or self.rates[0]
                if self.rate not in self.rates:
                    raise ValueError(f'Supported sample rates: {self.rates}')
                self._check('airspy_set_sample_type', self.device, 2)  # INT16_IQ
                self._check('airspy_set_samplerate', self.device, self.rate)
                self._check('airspy_set_rf_bias', self.device, 0)
                self._check('airspy_set_freq', self.device, self.frequency)
                self._check('airspy_set_linearity_gain', self.device, self.gain)
            with self.lock:
                self.latest = None
                self.last_capture = time.monotonic()
                self.last_frame = 0
                self.dropped = 0
                self.running = True
            if self.demo:
                self.worker = threading.Thread(target=self._demo, daemon=True)
                self.worker.start()
            else:
                self._check('airspy_start_rx', self.device, self.callback, None)
        except Exception as exc:
            self.close()
            self.error = str(exc)
            raise

    def _receive(self, ptr):
        try:
            t = ptr.contents
            now = time.monotonic()
            with self.lock:
                self.last_capture = now
                self.dropped += t.dropped_samples
                size = min(t.sample_count, WEB_CAPTURE_SIZE if self.mode == 'web' else N)
                if not self.running or size < N or now - self.last_frame < 1 / self.fps:
                    return 0
                payload = C.string_at(t.samples, size * 4)
                self.seq = (self.seq + 1) & 0xffffffff
                self.latest = HEADER.pack(b'ZIQ1', self.seq, self.frequency, self.rate,
                                          size, self.gain, self.dropped) + payload
                self.last_frame = now
        except Exception as exc:
            self.error = f'Capture: {exc}'
            return -1
        return 0

    def _demo(self):
        import random
        rng = random.Random(42)
        while not self.stop_event.is_set():
            # A tone plus noise, available only in explicit demo mode.
            size = WEB_CAPTURE_SIZE if self.mode == 'web' else N
            samples = (C.c_int16 * (size * 2))()
            for i in range(size):
                angle = 2 * math.pi * 321 * i / N
                samples[2*i] = int(12000 * math.cos(angle) + rng.gauss(0, 180))
                samples[2*i+1] = int(12000 * math.sin(angle) + rng.gauss(0, 180))
            t = Transfer(None, None, C.cast(samples, C.c_void_p), size, 0, 2)
            self._receive(C.pointer(t))
            self.stop_event.wait(1 / self.fps)

    def configure(self, **changes):
        if set(changes) - {'frequency', 'gain', 'rate', 'mode'}:
            raise ValueError('Unknown setting')
        with self.control_lock:
            frequency = changes.get('frequency', self.frequency)
            gain = changes.get('gain', self.gain)
            rate = changes.get('rate', self.rate)
            mode = changes.get('mode', self.mode)
            if type(frequency) is not int or not 24_000_000 <= frequency <= 1_800_000_000:
                raise ValueError('Frequency must be 24–1800 MHz')
            if type(gain) is not int or not 0 <= gain <= 21:
                raise ValueError('Linearity gain must be 0–21')
            if (rate is not None or 'rate' in changes) and (type(rate) is not int or rate not in self.rates):
                raise ValueError(f'Supported sample rates: {self.rates}')
            if mode not in ('lcd', 'web'):
                raise ValueError('Mode must be lcd or web')
            if mode == 'lcd' and not self.lcd_available:
                raise ValueError('Headless session: LCD is unavailable; launch CardputerSDR from APPLaunch')
            # Stop before changing metadata: no stale samples under a new frequency.
            restart = (frequency, gain, rate) != (self.frequency, self.gain, self.rate)
            restart = restart or not self.status()['running']
            if restart:
                self.close()
            with self.lock:
                if mode != self.mode:
                    self.latest = None
                self.frequency, self.gain, self.rate, self.mode = frequency, gain, rate, mode
                self.fps = 15 if mode == 'web' else 8
            if restart or not self.running:
                self.open()

    def frame(self, *, mode=None):
        with self.lock:
            if mode is not None and self.mode != mode:
                return None
            # Never show a frozen spectrum as if it were live after unplugging.
            return self.latest if self.running and time.monotonic() - self.last_capture < 2 else None

    def status(self):
        with self.lock:
            stalled = self.running and time.monotonic() - self.last_capture > 2
            return dict(version=__version__, demo=self.demo, frequency=self.frequency,
                        gain=self.gain, rate=self.rate, rates=self.rates, mode=self.mode,
                        running=self.running and not stalled, lcd_available=self.lcd_available, sequence=self.seq,
                        dropped=self.dropped, fps=self.fps,
                        error=self.error or ('Airspy stream stopped; check USB and press R' if stalled else ''),
                        transport='int16-iq-snapshots', fft_size=N, fft_sizes=WEB_FFT_SIZES)

    def close(self):
        with self.control_lock:
            with self.lock:
                self.running = False
                self.latest = None
            self.stop_event.set()
            if self.worker:
                self.worker.join(timeout=3)
                self.worker = None
            if self.device.value and self.lib:
                self.lib.airspy_stop_rx(self.device)
                self.lib.airspy_close(self.device)
                self.device = C.c_void_p()
