#!/usr/bin/env python3
"""Generate pre-signed Amiibo UID variants for button-driven selection.

The format/crypto flow follows the MIT-licensed amiitool implementation used
by Pixl.js.  No retail keys are included; the caller must supply its own
160-byte key_retail.bin.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import random
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

NTAG215_SIZE = 540
INTERNAL_SIZE = 520
MASTER_KEY_SIZE = 80
KEY_FILE_SIZE = 160
UID_SIZE = 7

HMAC_POS_DATA = 0x008
HMAC_POS_TAG = 0x1B4
CIPHER_START = 0x02C
CIPHER_END = CIPHER_START + 0x188


@dataclass(frozen=True)
class MasterKey:
    hmac_key: bytes
    type_string: bytes
    magic_bytes: bytes
    xor_pad: bytes


def parse_master_key(raw: bytes) -> MasterKey:
    if len(raw) != MASTER_KEY_SIZE:
        raise ValueError("主密钥块必须是 80 字节")
    magic_size = raw[31]
    if magic_size > 16:
        raise ValueError(f"非法 magicBytesSize: {magic_size}")
    type_field = raw[16:30]
    terminator = type_field.find(b"\0")
    if terminator < 0:
        raise ValueError("主密钥 typeString 缺少 NUL 终止符")
    return MasterKey(
        hmac_key=raw[0:16],
        type_string=type_field[: terminator + 1],
        magic_bytes=raw[32 : 32 + magic_size],
        xor_pad=raw[48:80],
    )


def parse_key_file(raw: bytes) -> tuple[MasterKey, MasterKey]:
    if len(raw) != KEY_FILE_SIZE:
        raise ValueError(f"key_retail.bin 必须是 160 字节，当前为 {len(raw)} 字节")
    return parse_master_key(raw[:80]), parse_master_key(raw[80:])


def tag_to_internal(tag: bytes) -> bytearray:
    if len(tag) != NTAG215_SIZE:
        raise ValueError("Amiibo dump 必须是 540 字节")
    internal = bytearray(INTERNAL_SIZE)
    internal[0x000:0x008] = tag[0x008:0x010]
    internal[0x008:0x028] = tag[0x080:0x0A0]
    internal[0x028:0x04C] = tag[0x010:0x034]
    internal[0x04C:0x1B4] = tag[0x0A0:0x208]
    internal[0x1B4:0x1D4] = tag[0x034:0x054]
    internal[0x1D4:0x1DC] = tag[0x000:0x008]
    internal[0x1DC:0x208] = tag[0x054:0x080]
    return internal


def internal_to_tag(internal: bytes, template: bytes) -> bytes:
    if len(internal) != INTERNAL_SIZE or len(template) != NTAG215_SIZE:
        raise ValueError("内部数据或模板长度不正确")
    tag = bytearray(template)
    tag[0x008:0x010] = internal[0x000:0x008]
    tag[0x080:0x0A0] = internal[0x008:0x028]
    tag[0x010:0x034] = internal[0x028:0x04C]
    tag[0x0A0:0x208] = internal[0x04C:0x1B4]
    tag[0x034:0x054] = internal[0x1B4:0x1D4]
    tag[0x000:0x008] = internal[0x1D4:0x1DC]
    tag[0x054:0x080] = internal[0x1DC:0x208]
    return bytes(tag)


def calc_seed(internal: bytes) -> bytes:
    return (
        internal[0x029:0x02B]
        + bytes(14)
        + internal[0x1D4:0x1DC]
        + internal[0x1D4:0x1DC]
        + internal[0x1E8:0x208]
    )


def derive_keys(master: MasterKey, internal: bytes) -> tuple[bytes, bytes, bytes]:
    base_seed = calc_seed(internal)
    leading = 16 - len(master.magic_bytes)
    prepared = (
        master.type_string
        + base_seed[:leading]
        + master.magic_bytes
        + base_seed[0x10:0x20]
        + bytes(a ^ b for a, b in zip(base_seed[0x20:0x40], master.xor_pad))
    )
    derived = b"".join(
        hmac.new(master.hmac_key, counter.to_bytes(2, "big") + prepared, hashlib.sha256).digest()
        for counter in range(2)
    )
    return derived[0:16], derived[16:32], derived[32:48]


def aes_ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    operation = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    return operation.update(data) + operation.finalize()


def unpack_amiibo(tag: bytes, data_master: MasterKey, tag_master: MasterKey) -> bytes:
    internal = tag_to_internal(tag)
    data_aes, data_iv, data_hmac_key = derive_keys(data_master, internal)
    _, _, tag_hmac_key = derive_keys(tag_master, internal)

    plain = bytearray(INTERNAL_SIZE)
    plain[0x000:0x008] = internal[0x000:0x008]
    plain[0x028:0x02C] = internal[0x028:0x02C]
    plain[CIPHER_START:CIPHER_END] = aes_ctr(
        data_aes, data_iv, internal[CIPHER_START:CIPHER_END]
    )
    plain[0x1D4:0x208] = internal[0x1D4:0x208]

    tag_signature = hmac.new(tag_hmac_key, plain[0x1D4:0x208], hashlib.sha256).digest()
    plain[HMAC_POS_TAG : HMAC_POS_TAG + 32] = tag_signature
    data_signature = hmac.new(data_hmac_key, plain[0x029:0x208], hashlib.sha256).digest()
    plain[HMAC_POS_DATA : HMAC_POS_DATA + 32] = data_signature

    if not hmac.compare_digest(data_signature, internal[HMAC_POS_DATA : HMAC_POS_DATA + 32]):
        raise ValueError("dump 的数据签名校验失败；密钥或 dump 不匹配")
    if not hmac.compare_digest(tag_signature, internal[HMAC_POS_TAG : HMAC_POS_TAG + 32]):
        raise ValueError("dump 的标签签名校验失败；密钥或 dump 不匹配")
    return bytes(plain)


def pack_amiibo(
    plain: bytes, template: bytes, data_master: MasterKey, tag_master: MasterKey
) -> bytes:
    if len(plain) != INTERNAL_SIZE:
        raise ValueError("内部明文必须是 520 字节")
    data_aes, data_iv, data_hmac_key = derive_keys(data_master, plain)
    _, _, tag_hmac_key = derive_keys(tag_master, plain)

    internal = bytearray(INTERNAL_SIZE)
    tag_signature = hmac.new(tag_hmac_key, plain[0x1D4:0x208], hashlib.sha256).digest()
    data_material = plain[0x029:0x1B4] + tag_signature + plain[0x1D4:0x208]
    data_signature = hmac.new(data_hmac_key, data_material, hashlib.sha256).digest()

    internal[HMAC_POS_DATA : HMAC_POS_DATA + 32] = data_signature
    internal[HMAC_POS_TAG : HMAC_POS_TAG + 32] = tag_signature
    internal[0x000:0x008] = plain[0x000:0x008]
    internal[0x028:0x02C] = plain[0x028:0x02C]
    internal[CIPHER_START:CIPHER_END] = aes_ctr(
        data_aes, data_iv, plain[CIPHER_START:CIPHER_END]
    )
    internal[0x1D4:0x208] = plain[0x1D4:0x208]
    return internal_to_tag(internal, template)


def get_uid(tag: bytes) -> bytes:
    return bytes((tag[0], tag[1], tag[2], tag[4], tag[5], tag[6], tag[7]))


def replace_uid(plain: bytearray, uid: bytes, salt: bytes) -> None:
    if len(uid) != UID_SIZE or uid[0] != 0x04:
        raise ValueError("UID 必须是以 0x04 开头的 7 字节值")
    if len(salt) != 32:
        raise ValueError("keygen salt 必须是 32 字节")
    plain[0x000] = uid[3] ^ uid[4] ^ uid[5] ^ uid[6]
    plain[0x1D4:0x1D7] = uid[0:3]
    plain[0x1D7] = 0x88 ^ uid[0] ^ uid[1] ^ uid[2]
    plain[0x1D8:0x1DC] = uid[3:7]
    plain[0x1E8:0x208] = salt


def validate_bcc(tag: bytes) -> None:
    expected0 = 0x88 ^ tag[0] ^ tag[1] ^ tag[2]
    expected1 = tag[4] ^ tag[5] ^ tag[6] ^ tag[7]
    if tag[3] != expected0 or tag[8] != expected1:
        raise ValueError("生成结果的 UID BCC 校验失败")


def format_header(tags: list[bytes]) -> str:
    if not tags:
        return """/* UID randomization disabled: key_retail.bin was not supplied. */
#ifndef AMIIBO_TAG_UID_POOL_H
#define AMIIBO_TAG_UID_POOL_H
#define AMIIBO_UID_POOL_COUNT 0u
#endif
"""

    rows: list[str] = []
    for tag_index, tag in enumerate(tags):
        uid = get_uid(tag).hex(":").upper()
        rows.append(f"    {{ /* {tag_index}: {uid} */")
        for offset in range(0, len(tag), 12):
            chunk = tag[offset : offset + 12]
            rows.append("        " + ", ".join(f"0x{value:02X}" for value in chunk) + ",")
        rows.append("    },")
    return f"""/* Auto-generated by tools/generate_uid_pool.py.
 * Contains pre-signed variants, never the retail master key.
 */
#ifndef AMIIBO_TAG_UID_POOL_H
#define AMIIBO_TAG_UID_POOL_H

#include <stdint.h>
#define AMIIBO_UID_POOL_COUNT {len(tags)}u
static const uint8_t amiibo_uid_pool[AMIIBO_UID_POOL_COUNT][{NTAG215_SIZE}] = {{
{chr(10).join(rows)}
}};
#endif
"""


def generate_pool(
    tag: bytes, key_data: bytes, count: int, seed: int | None = None
) -> list[bytes]:
    if count < 1 or count > 256:
        raise ValueError("UID 变体数量必须在 1..256")
    data_master, tag_master = parse_key_file(key_data)
    original_plain = unpack_amiibo(tag, data_master, tag_master)
    original_uid = get_uid(tag)
    seen = {original_uid}
    result: list[bytes] = []
    rng = random.Random(seed) if seed is not None else None

    def random_bytes(length: int) -> bytes:
        if rng is None:
            return os.urandom(length)
        return bytes(rng.randrange(256) for _ in range(length))

    while len(result) < count:
        uid = b"\x04" + random_bytes(6)
        if uid in seen:
            continue
        seen.add(uid)
        modified = bytearray(original_plain)
        replace_uid(modified, uid, random_bytes(32))
        generated = pack_amiibo(modified, tag, data_master, tag_master)
        validate_bcc(generated)
        if get_uid(generated) != uid:
            raise ValueError("生成结果 UID 不一致")
        unpack_amiibo(generated, data_master, tag_master)
        result.append(generated)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="生成按键随机 UID 所需的预签名 Amiibo 池")
    parser.add_argument("--dump", type=Path, help="540 字节的自有 Amiibo dump")
    parser.add_argument("--key", type=Path, help="自己合法取得的 160 字节 key_retail.bin")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=32)
    parser.add_argument("--seed", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--disabled", action="store_true")
    args = parser.parse_args()

    if args.disabled:
        tags: list[bytes] = []
    else:
        if args.dump is None or args.key is None:
            parser.error("未使用 --disabled 时必须同时提供 --dump 和 --key")
        tags = generate_pool(args.dump.read_bytes(), args.key.read_bytes(), args.count, args.seed)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(format_header(tags), encoding="utf-8")
    if tags:
        print(f"已生成 {len(tags)} 个已重签名 UID 变体：{args.output}")
    else:
        print(f"未提供密钥，按键随机 UID 已禁用：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
