#!/usr/bin/env python3
"""Cross-compile the selector for the console's ARMv7 hard-float Linux ABI."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Choose a new output file')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Firmware libc is 2.29. Limit imported symbols to the older 2.17 ABI.
    with tempfile.TemporaryDirectory(prefix='atari-menu-compile-') as tmp:
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=tmp+'/cache',
                   ZIG_LOCAL_CACHE_DIR=tmp+'/local')
        binary = Path(tmp)/'menu'
        subprocess.run(['zig', 'cc', '-target', 'arm-linux-gnueabihf.2.17',
                        '-mcpu=cortex_a7', '-std=c11', '-Os', '-Wall', '-Wextra',
                        '-Werror', '-fno-pie', '-no-pie', '-Wl,--hash-style=both',
                        '-s', str(HERE/'payload/menu.c'), '-ldl', '-o', str(binary)],
                       env=env, check=True)
        data = binary.read_bytes()
        if data[:6] != b'\x7fELF\x01\x01' or data[18:20] != b'\x28\x00':
            raise RuntimeError('Compiler did not produce little-endian ARM ELF32')
        with output.open('xb') as stream:
            stream.write(data)
        output.chmod(0o755)
    print(f'Built {output} ({len(data)} bytes)')


if __name__ == '__main__':
    main()
