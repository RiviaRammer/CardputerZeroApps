#!/usr/bin/env python3
"""Export pre-signed variants from the supplied ADV generator as raw NTAG215 pages."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=64)
    args = parser.parse_args()

    try:
        from generate_uid_pool import generate_pool, validate_bcc

        dump = args.dump.read_bytes()
        if len(dump) != 540 or dump[0] != 4:
            raise ValueError("expected a 540-byte Amiibo dump with an NXP UID")
        validate_bcc(dump)
        variants = generate_pool(dump, args.key.read_bytes(), args.count)
        # Create a new file so a mistyped path cannot overwrite the backup or key.
        with args.output.open("xb") as output:
            output.write(b"".join(variants))
    except (ImportError, OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")

    print(f"Wrote {len(variants)} pre-signed variants to {args.output}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
