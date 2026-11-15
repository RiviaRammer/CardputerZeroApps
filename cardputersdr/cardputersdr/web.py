"""Bounded, pull-driven WebSocket IQ transport; no server-side FFT."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web, WSMsgType
from .radio import HEADER, N, WEB_FFT_SIZES

ROOT = Path(__file__).parent / 'static'
IQ_REQUESTS = {'next': N, **{f'next {size}': size for size in WEB_FFT_SIZES}}


def same_origin(request):
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        raise web.HTTPForbidden(text='Cross-origin control is disabled')


def create_app(radio):
    app = web.Application(client_max_size=2048)
    clients = set()

    async def index(request):
        return web.FileResponse(ROOT / 'index.html', headers={'Cache-Control': 'no-cache'})

    async def asset(request):
        name = request.match_info['name']
        if name not in ('app.js', 'fft.js', 'worker.js', 'style.css'):
            raise web.HTTPNotFound()
        return web.FileResponse(ROOT / name, headers={'Cache-Control': 'no-cache'})

    async def status(request):
        return web.json_response(radio.status())

    async def control(request):
        same_origin(request)
        if request.content_type != 'application/json':
            raise web.HTTPUnsupportedMediaType()
        try:
            values = await request.json()
            if not isinstance(values, dict):
                raise ValueError('Expected a JSON object')
            await asyncio.to_thread(radio.configure, **values)
        except (ValueError, TypeError, RuntimeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
        return web.json_response(radio.status())

    async def stream(request):
        same_origin(request)
        if len(clients) >= 4:
            raise web.HTTPServiceUnavailable(text='Four viewers already connected')
        ws = web.WebSocketResponse(max_msg_size=128, heartbeat=20, compress=False)
        clients.add(ws)
        try:
            await ws.prepare(request)
            last = None
            async for message in ws:
                size = IQ_REQUESTS.get(message.data) if message.type == WSMsgType.TEXT else None
                if size is None:
                    await ws.close(code=1008, message=b'Expected next or next <supported FFT size>')
                    break
                # One request -> at most one frame. Browser ACKs after worker FFT.
                await asyncio.sleep(1 / radio.fps)
                frame = radio.frame(mode='web')
                if frame and frame[4:8] != last:
                    header = list(HEADER.unpack_from(frame))
                    if header[4] >= size:
                        header[4] = size
                        packet = HEADER.pack(*header) + frame[HEADER.size:HEADER.size + size * 4]
                        await asyncio.wait_for(ws.send_bytes(packet), timeout=3)
                        last = frame[4:8]
                        continue
                await ws.send_str(json.dumps(radio.status()))
        except (ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            clients.discard(ws)
        return ws

    async def shutdown(app):
        await asyncio.gather(*(ws.close(code=1001, message=b'CardputerSDR exiting')
                               for ws in tuple(clients)), return_exceptions=True)

    app.router.add_get('/', index)
    app.router.add_get('/{name:app.js|fft.js|worker.js|style.css}', asset)
    app.router.add_get('/api/status', status)
    app.router.add_post('/api/control', control)
    app.router.add_get('/iq', stream)
    app.on_shutdown.append(shutdown)
    return app


class Server:
    def __init__(self, radio, bind, port):
        self.radio, self.bind, self.port = radio, bind, port
        self.runner = None

    async def start(self):
        self.runner = web.AppRunner(create_app(self.radio), shutdown_timeout=2)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, self.bind, self.port).start()
        except Exception:
            await self.runner.cleanup()
            self.runner = None
            raise

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
