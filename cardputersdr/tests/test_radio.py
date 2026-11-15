import ctypes as C
import json
import math
from pathlib import Path
import subprocess
import time
import unittest
import numpy as np
from cardputersdr.radio import Radio,Transfer,N,HEADER,spectrum

def tone(bin=321,level=.25,size=N):
    phase=np.arange(size)*2*np.pi*bin/size
    return (np.column_stack((np.cos(phase),np.sin(phase)))*32768*level).astype('<i2').tobytes()

class RadioTests(unittest.TestCase):
    def test_retry_before_first_successful_open(self):
        r=Radio(demo=True)
        try:
            self.assertIsNone(r.rate)
            r.configure(mode='web')
            self.assertTrue(r.running)
            self.assertEqual(r.rate,3000000)
        finally:r.close()

    def test_headless_rejects_lcd(self):
        r=Radio(demo=True);r.lcd_available=False
        with self.assertRaises(ValueError):r.configure(mode='lcd')

    def test_fft_frequency_sign_and_dbfs(self):
        for bin in (-621,321):
            db=spectrum(tone(bin))
            self.assertEqual(int(db.argmax()),N//2+bin)
            self.assertAlmostEqual(float(db.max()),20*math.log10(.25),places=2)

    def test_callback_snapshot_header_and_owned_memory(self):
        r=Radio(demo=True);r.rate=3000000;r.running=True
        source=C.create_string_buffer(tone());t=Transfer(None,None,C.cast(source,C.c_void_p),N,17,2)
        self.assertEqual(r._receive(C.pointer(t)),0)
        frame=r.frame();self.assertEqual(len(frame),32+4*N)
        self.assertEqual(HEADER.unpack_from(frame),(b'ZIQ1',1,100000000,3000000,N,10,17))
        C.memset(source,0,N*4);self.assertEqual(r.frame()[32:],frame[32:])
        r.last_capture=time.monotonic()-3
        self.assertIsNone(r.frame());self.assertFalse(r.status()['running'])

    def test_validation_and_retune(self):
        r=Radio(demo=True);r.open()
        try:
            for changes in ({'frequency':1},{'gain':22},{'rate':1},{'mode':'both'},{'gain':True},{'unknown':1}):
                with self.assertRaises(ValueError):r.configure(**changes)
            r.configure(frequency=145500000,mode='web',gain=5)
            deadline=time.monotonic()+2
            while not r.frame() and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(HEADER.unpack_from(r.frame())[2],145500000)
            self.assertEqual(r.status()['mode'],'web')
        finally:r.close()
        self.assertIsNone(r.frame())

    def test_browser_fft_matches_numpy(self):
        import base64
        js=Path('cardputersdr/static/fft.js').resolve()
        code="const f=require(process.argv[1]);const b=Buffer.from(require('node:fs').readFileSync(0,'utf8'),'base64');console.log(JSON.stringify(Array.from(f.spectrum(b.buffer.slice(b.byteOffset,b.byteOffset+b.length)).db)))"
        for size in (1024, N, 16384):
            with self.subTest(size=size):
                packet=HEADER.pack(b'ZIQ1',1,145500000,3000000,size,10,0)+tone(-133,size=size)
                values=json.loads(subprocess.check_output(['node','-e',code,str(js)],input=base64.b64encode(packet)))
                np.testing.assert_allclose(values,spectrum(packet[32:]),atol=.003)

if __name__=='__main__':unittest.main()
