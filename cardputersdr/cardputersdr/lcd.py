"""320 x 170 framebuffer UI. Web mode never calls spectrum()."""
import asyncio
import ctypes as C
import os
import math
import struct
import time
from PIL import Image, ImageDraw, ImageFont
from .radio import HEADER, spectrum

BG, FG, DIM, ACCENT = '#09131e', '#e0edf0', '#829aaf', '#53e0c4'
FONT = ImageFont.load_default_imagefont()


class Framebuffer:
    def __init__(self):
        import fcntl
        import mmap
        class Fix(C.Structure):
            _fields_ = [('id', C.c_char*16), ('smem_start', C.c_ulong), ('smem_len', C.c_uint32),
                        ('type', C.c_uint32), ('type_aux', C.c_uint32), ('visual', C.c_uint32),
                        ('xpanstep', C.c_uint16), ('ypanstep', C.c_uint16), ('ywrapstep', C.c_uint16),
                        ('line_length', C.c_uint32), ('mmio_start', C.c_ulong), ('mmio_len', C.c_uint32),
                        ('accel', C.c_uint32), ('capabilities', C.c_uint16), ('reserved', C.c_uint16*2)]
        self.fd = os.open(os.environ.get('APPLAUNCH_LINUX_FBDEV_DEVICE', '/dev/fb0'), os.O_RDWR)
        try:
            var = bytearray(160)
            fcntl.ioctl(self.fd, 0x4600, var)
            w, h, _, _, self.xoff, self.yoff, self.bpp, _ = struct.unpack_from('8I', var)
            self.channels = [struct.unpack_from('3I', var, 32+i*12) for i in range(4)]
            fixbuf = bytearray(C.sizeof(Fix))
            fcntl.ioctl(self.fd, 0x4602, fixbuf)
            fix = Fix.from_buffer_copy(fixbuf)
            if (w, h) != (320, 170) or self.bpp not in (16, 32):
                raise ValueError('CardputerSDR needs a 320x170 16/32-bit framebuffer')
            if any(msb for _, _, msb in self.channels):
                raise ValueError('msb_right framebuffer unsupported')
            self.stride = fix.line_length
            self.mem = mmap.mmap(self.fd, fix.smem_len, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        except Exception:
            os.close(self.fd)
            raise

    def show(self, image):
        import numpy as np
        rgb = np.asarray(image, dtype=np.uint32)
        pixel = np.zeros((170, 320), dtype=np.uint32)
        for index, (offset, length, _) in enumerate(self.channels):
            if length:
                values = rgb[:, :, index] if index < 3 else 255
                pixel |= (((values * ((1 << length)-1) + 127)//255) << offset)
        n = self.bpp//8
        raw = pixel.astype('<u2' if n == 2 else '<u4').tobytes()
        for y in range(170):
            start = (y+self.yoff)*self.stride+self.xoff*n
            self.mem[start:start+320*n] = raw[y*320*n:(y+1)*320*n]

    def close(self):
        self.mem.close()
        os.close(self.fd)


class View:
    def __init__(self):
        self.waterfall = Image.new('RGB', (304, 35), BG)
        self.values = None
        self.sequence = None
        self.config = None
        self.step_index = 2
        self.steps = [1000, 10000, 100000, 1000000]
        self.entry = ''
        self.message = ''

    def render(self, state, frame=None, address='No LAN address'):
        image = Image.new('RGB', (320,170), BG)
        draw = ImageDraw.Draw(image)
        def text(x,y,s,color=FG):
            draw.text((x,y),str(s),font=FONT,fill=color)
        text(8,3,'CardputerSDR',ACCENT)
        text(164,3,('DEMO ' if state['demo'] else 'AIRSPY ') + state['mode'].upper(),DIM)
        draw.line((8,19,312,19), fill='#294052')
        label=f"{state['frequency']/1e6:.3f} MHz"
        title=Image.new('RGB',(len(label)*6,12),BG)
        ImageDraw.Draw(title).text((0,0),label,font=FONT,fill=ACCENT)
        image.paste(title.resize((title.width*2,24),Image.Resampling.NEAREST),(8,23))
        text(237,24,f"{(state['rate'] or 0)/1e6:g} MS/s",DIM)
        text(237,36,f"GAIN {state['gain']:02}",DIM)
        if state['mode'] == 'web':
            text(8,51,'WEB SPECTRUM',ACCENT)
            text(8,72,'Open on phone / computer:')
            text(8,90,address,ACCENT)
            text(8,112,'FFT runs in your browser',DIM)
            text(8,128,'L: LCD   R: reconnect',DIM)
        else:
            config = (state['frequency'],state['rate'],state['gain'])
            if config != self.config:
                self.waterfall.paste(BG,(0,0,304,35))
                self.values = None
                self.config = config
            if frame and frame[4:8] != self.sequence:
                import numpy as np
                db = spectrum(frame[HEADER.size:])
                edges = np.linspace(0,len(db),305,dtype=int)
                self.values = [float(db[edges[i]:edges[i+1]].max()) for i in range(304)]
                self.sequence = frame[4:8]
                self.waterfall.paste(self.waterfall.crop((0,0,304,34)),(0,1))
                row = []
                for value in self.values:
                    level = max(0,min(1,(value+100)/65))
                    row.append((int(255*max(0,(level-.65)/.35)),int(230*min(1,level*1.7)),int(25+180*math.sin(level*math.pi))))
                line = Image.new('RGB',(304,1)); line.putdata(row); self.waterfall.paste(line,(0,0))
            for y in (51,67,83,99):
                draw.line((8,y,311,y),fill='#1b3040')
            text(9,49,'0',DIM);text(9,89,'-100',DIM)
            if self.values is not None and frame:
                points=[(8+x,100-int(max(0,min(1,(v+100)/100))*49)) for x,v in enumerate(self.values)]
                draw.line(points,fill=ACCENT,width=1)
                image.paste(self.waterfall.crop((0,0,304,27)),(8,115))
            else:
                text(56,71,'Waiting for Airspy...',DIM)
            rate=state['rate'] or 0
            text(8,102,f"{(state['frequency']-rate/2)/1e6:.3f}",DIM)
            text(130,102,f"{state['frequency']/1e6:.3f}",DIM)
            right=f"{(state['frequency']+rate/2)/1e6:.3f}"
            text(312-len(right)*6,102,right,DIM)
            text(8,145,f"STEP {self.steps[self.step_index]/1000:g}k  W:Web S:step +/-:gain",DIM)
        if self.entry:
            draw.rectangle((5,112,315,148),fill='#21364a')
            text(10,116,'Tune MHz: '+self.entry,ACCENT)
            text(10,132,'Enter: apply   Esc: cancel',DIM)
        error = state['error'] or self.message
        if error:
            draw.rectangle((5,110,315,149),fill='#442526')
            text(9,113,error[:50],'#ffb0a0')
            text(9,128,error[50:100] or 'R: retry   Esc: exit','#ffb0a0')
        text(8,158,'Z/C:tune  0-9:MHz  L:LCD  Esc:exit',DIM)
        return image


async def run(radio, stop, address):
    from evdev import InputDevice, ecodes, list_devices
    fb = Framebuffer()
    devices = []
    view = View()
    previous = None
    try:
        for path in list_devices():
            device = InputDevice(path)
            if ecodes.KEY_ENTER in device.capabilities().get(ecodes.EV_KEY, []):
                device.grab()
                devices.append(device)
            else:
                device.close()
        if not devices:
            raise RuntimeError('No keyboard access; check input group')
        digits = {getattr(ecodes, 'KEY_'+str(n)): str(n) for n in range(10)}
        while not stop.is_set():
            state = radio.status()
            image = view.render(state, radio.frame(mode='lcd') if state['mode']=='lcd' else None, address)
            pixels = image.tobytes()
            if pixels != previous:
                fb.show(image)
                previous = pixels
            for device in devices:
                try:
                    events = list(device.read())
                except BlockingIOError:
                    continue
                for event in events:
                    if event.type != ecodes.EV_KEY or event.value not in (1,2):
                        continue
                    key = event.code
                    try:
                        changes = {}
                        if key == ecodes.KEY_ESC:
                            if view.entry: view.entry = ''
                            else: stop.set()
                        elif key in digits and len(view.entry)<12:
                            view.entry += digits[key]
                        elif key == ecodes.KEY_DOT and '.' not in view.entry:
                            view.entry += '.'
                        elif key == ecodes.KEY_BACKSPACE:
                            view.entry = view.entry[:-1]
                        elif key == ecodes.KEY_ENTER and view.entry:
                            changes['frequency'] = round(float(view.entry)*1e6)
                            view.entry = ''
                        elif key in (ecodes.KEY_W, ecodes.KEY_L):
                            changes['mode'] = 'web' if key == ecodes.KEY_W else 'lcd'
                        elif key == ecodes.KEY_S and event.value==1:
                            view.step_index = (view.step_index+1)%len(view.steps)
                        elif key in (ecodes.KEY_LEFT,ecodes.KEY_Z,ecodes.KEY_RIGHT,ecodes.KEY_C):
                            direction = -1 if key in (ecodes.KEY_LEFT,ecodes.KEY_Z) else 1
                            changes['frequency'] = max(24_000_000,min(1_800_000_000,radio.frequency+direction*view.steps[view.step_index]))
                        elif key in (ecodes.KEY_MINUS,ecodes.KEY_EQUAL,ecodes.KEY_UP,ecodes.KEY_DOWN,ecodes.KEY_F,ecodes.KEY_X):
                            direction = -1 if key in (ecodes.KEY_MINUS,ecodes.KEY_DOWN,ecodes.KEY_X) else 1
                            changes['gain'] = max(0,min(21,radio.gain+direction))
                        elif key == ecodes.KEY_R and event.value==1:
                            await asyncio.to_thread(radio.close)
                            await asyncio.to_thread(radio.open)
                        if changes:
                            await asyncio.to_thread(radio.configure,**changes)
                        view.message = ''
                    except (ValueError,RuntimeError) as exc:
                        view.message = str(exc)
            await asyncio.sleep(.12 if radio.mode=='lcd' else .2)
    finally:
        for device in devices:
            device.close()
        fb.close()
