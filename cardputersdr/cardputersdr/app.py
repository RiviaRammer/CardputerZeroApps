import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import sys
from .radio import Radio
from .web import Server


def local_address(port):
    try:
        # UDP connect selects a route without sending a packet.
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1',9))
            return f'{s.getsockname()[0]}:{port}'
    except OSError:
        return f'No network (port {port})'


async def run(args):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):
        try:
            loop.add_signal_handler(sig,stop.set)
        except NotImplementedError:
            signal.signal(sig,lambda *_:loop.call_soon_threadsafe(stop.set))
    radio = Radio(args.demo,args.frequency,args.gain,args.rate)
    radio.lcd_available = not args.headless
    radio.mode = 'web' if args.headless else args.mode
    radio.fps = 15 if radio.mode=='web' else 8
    server = Server(radio,args.bind,args.port)
    lock = None
    try:
        if sys.platform == 'linux':
            import fcntl
            runtime = Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))
            if not runtime.is_dir():
                runtime=Path.home()/'.cache'/'cardputersdr'; runtime.mkdir(parents=True,exist_ok=True)
            lock = open(runtime/'cardputersdr.lock','w')
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise RuntimeError('CardputerSDR is already running for this user')
        try:
            await asyncio.to_thread(radio.open)
        except (RuntimeError,OSError) as exc:
            print(f'Airspy: {exc}',file=sys.stderr,flush=True)
        await server.start()
        print(f'CardputerSDR http://{local_address(args.port)}/ mode={radio.mode}',flush=True)
        if args.headless:
            await stop.wait()
        else:
            from .lcd import run as lcd_run
            await lcd_run(radio,stop,local_address(args.port))
    finally:
        await server.close()
        await asyncio.to_thread(radio.close)
        if lock: lock.close()


def main():
    p=argparse.ArgumentParser(description='CardputerSDR: Airspy LCD / browser spectrum')
    p.add_argument('--headless',action='store_true',help='Web only, no framebuffer/keyboard')
    p.add_argument('--mode',choices=('lcd','web'),default='lcd')
    p.add_argument('--bind',default='0.0.0.0')
    p.add_argument('--port',type=int,default=1420)
    p.add_argument('--frequency',type=int,default=100_000_000,help='Center frequency in Hz')
    p.add_argument('--gain',type=int,default=10)
    p.add_argument('--rate',type=int,help='Supported IQ sample rate in samples/s; default lowest')
    p.add_argument('--demo',action='store_true',help='Explicit simulated IQ, no Airspy')
    args=p.parse_args()
    if not 24_000_000<=args.frequency<=1_800_000_000 or not 0<=args.gain<=21 or not 1<=args.port<=65535:
        p.error('Frequency: 24–1800 MHz, gain: 0–21, port: 1–65535')
    try:
        asyncio.run(run(args))
    except (RuntimeError,OSError) as exc:
        print(f'CardputerSDR: {exc}',file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
