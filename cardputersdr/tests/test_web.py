import asyncio
import unittest
from aiohttp import web,ClientSession
from cardputersdr.radio import Radio,HEADER
from cardputersdr.web import create_app

class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.radio=Radio(demo=True);self.radio.open();self.radio.configure(mode='web')
        self.runner=web.AppRunner(create_app(self.radio));await self.runner.setup()
        self.site=web.TCPSite(self.runner,'127.0.0.1',0);await self.site.start()
        self.url='http://127.0.0.1:'+str(self.site._server.sockets[0].getsockname()[1])
        self.client=ClientSession()
    async def asyncTearDown(self):
        await self.client.close();await self.runner.cleanup();self.radio.close()
    async def test_control_and_origin(self):
        async with self.client.get(self.url+'/') as r:self.assertIn('CardputerSDR',await r.text())
        async with self.client.post(self.url+'/api/control',json={'frequency':144800000}) as r:self.assertEqual((await r.json())['frequency'],144800000)
        async with self.client.post(self.url+'/api/control',json={'gain':22}) as r:self.assertEqual(r.status,400)
        async with self.client.post(self.url+'/api/control',json={'gain':3},headers={'Origin':'http://evil.invalid'}) as r:self.assertEqual(r.status,403)
        async with self.client.get(self.url+'/../radio.py') as r:self.assertEqual(r.status,404)
    async def test_iq_demand_and_lcd_switch(self):
        async with self.client.ws_connect(self.url+'/iq') as ws:
            await ws.send_str('next');packet=await ws.receive(timeout=2)
            self.assertIsInstance(packet.data,bytes)
            self.assertEqual(HEADER.unpack_from(packet.data)[0],b'ZIQ1')
            with self.assertRaises(asyncio.TimeoutError):await ws.receive(timeout=.2)
            self.radio.configure(mode='lcd');await ws.send_str('next')
            self.assertEqual((await ws.receive_json(timeout=2))['mode'],'lcd')

    async def test_fft_size_is_per_viewer_and_matches_payload(self):
        async with self.client.ws_connect(self.url+'/iq') as first, self.client.ws_connect(self.url+'/iq') as second:
            for ws, request, size in ((first,'next 16384',16384), (second,'next',4096), (first,'next 1024',1024)):
                for _ in range(5):
                    await ws.send_str(request)
                    message = await ws.receive(timeout=2)
                    if isinstance(message.data,bytes):
                        break
                self.assertIsInstance(message.data,bytes)
                self.assertEqual(HEADER.unpack_from(message.data)[4],size)
                self.assertEqual(len(message.data),HEADER.size+size*4)
            await first.send_str('next 32768')
            await first.receive(timeout=2)
            self.assertEqual(first.close_code,1008)
if __name__=='__main__':unittest.main()
