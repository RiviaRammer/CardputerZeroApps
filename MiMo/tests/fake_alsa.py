"""Pipe-based ALSA stand-in: exercises real subprocess IO without sound hardware."""
from pathlib import Path
import sys
import time

if sys.argv[1] == "play":
    Path(sys.argv[2]).write_bytes(sys.stdin.buffer.read())
elif sys.argv[1] == "record":
    sys.stdout.buffer.write(b"\x01\x02" * 16000)
    sys.stdout.buffer.flush()
    time.sleep(10)
elif sys.argv[1] == "fail":
    print(sys.argv[2], file=sys.stderr)
    sys.exit(1)
elif sys.argv[1] == "hang":
    time.sleep(10)
