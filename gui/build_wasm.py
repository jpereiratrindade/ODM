"""Build the tiny GSD module without an external compiler.

Equivalent WAT (all inputs f64, altitude in m, sensor/focal in mm):
  (module (func (export "gsd") (param f64 f64 f64 f64) (result f64)
    local.get 0 local.get 1 f64.mul local.get 2 f64.div
    local.get 3 f64.div f64.const 100 f64.mul))
Formula: altitude * sensor_width / focal_length / image_width * 100 cm/px.
Only standard WebAssembly numeric instructions; no memory, imports or I/O.
"""
from pathlib import Path
import struct


def build():
    code = bytes.fromhex('00 20 00 20 01 a2 20 02 a3 20 03 a3 44') + struct.pack('<d', 100) + bytes.fromhex('a2 0b')
    sections = [
        (1, bytes.fromhex('01 60 04 7c 7c 7c 7c 01 7c')),
        (3, bytes.fromhex('01 00')),
        (7, bytes.fromhex('01 03') + b'gsd' + bytes.fromhex('00 00')),
        (10, bytes([1, len(code)]) + code),
    ]
    return b'\0asm\x01\0\0\0' + b''.join(bytes([kind, len(payload)]) + payload for kind, payload in sections)


if __name__ == '__main__':
    Path(__file__).with_name('web').joinpath('wasm/gsd.wasm').write_bytes(build())
