"""WeCom AES-CBC callback decryptor. Ported from Hermes wecom_crypto.py."""
from __future__ import annotations

import base64
import hashlib
import socket
import struct
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .base import BaseDecryptor


class PKCS7Encoder:
    block_size = 32

    @classmethod
    def encode(cls, text: bytes) -> bytes:
        amount_to_pad = cls.block_size - (len(text) % cls.block_size)
        if amount_to_pad == 0:
            amount_to_pad = cls.block_size
        return text + bytes([amount_to_pad]) * amount_to_pad

    @classmethod
    def decode(cls, decrypted: bytes) -> bytes:
        if not decrypted:
            raise ValueError("empty decrypted payload")
        pad = decrypted[-1]
        if pad < 1 or pad > cls.block_size:
            raise ValueError("invalid PKCS7 padding")
        if decrypted[-pad:] != bytes([pad]) * pad:
            raise ValueError("malformed PKCS7 padding")
        return decrypted[:-pad]


def _sha1_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    parts = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


class WXBizMsgCrypt:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str):
        if not encoding_aes_key or len(encoding_aes_key) != 43:
            raise ValueError("encoding_aes_key must be 43 chars")
        self.token = token
        self.receive_id = receive_id
        self.key = base64.b64decode(encoding_aes_key + "=")
        self.iv = self.key[:16]

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        plain = self.decrypt(msg_signature, timestamp, nonce, echostr)
        return plain.decode("utf-8")

    def decrypt(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> bytes:
        expected = _sha1_signature(self.token, timestamp, nonce, encrypt)
        if expected != msg_signature:
            raise ValueError("signature mismatch")
        cipher_text = base64.b64decode(encrypt)
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded = decryptor.update(cipher_text) + decryptor.finalize()
        plain = PKCS7Encoder.decode(padded)
        content = plain[16:]
        xml_length = socket.ntohl(struct.unpack("I", content[:4])[0])
        xml_content = content[4:4 + xml_length]
        receive_id = content[4 + xml_length:].decode("utf-8")
        if receive_id != self.receive_id:
            raise ValueError("receive_id mismatch")
        return xml_content


def _xml_to_dict(xml_text: str) -> dict:
    """Parse XML into a nested dict. Nested XML elements become sub-dicts."""
    root = ET.fromstring(xml_text)
    result = {}
    for child in root:
        text = child.text or ""
        if "<" in text:
            try:
                sub = ET.fromstring(f"<root>{text}</root>")
                nested = {}
                for sc in sub:
                    nested[sc.tag] = sc.text or ""
                result[child.tag] = nested
                continue
            except ET.ParseError:
                pass
        result[child.tag] = text
    return result


class WeComAESDecryptor(BaseDecryptor):
    """WeCom callback decryptor using AES-CBC."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.crypt = WXBizMsgCrypt(
            token=config["token"],
            encoding_aes_key=config["encoding_aes_key"],
            receive_id=config["receive_id"],
        )

    def verify(self, query: Dict[str, str]) -> Optional[str]:
        msg_signature = query.get("msg_signature", "")
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")
        echostr = query.get("echostr", "")
        try:
            return self.crypt.verify_url(msg_signature, timestamp, nonce, echostr)
        except Exception:
            return None

    def decrypt(self, body: bytes, query: Dict[str, str]) -> Optional[Dict[str, Any]]:
        msg_signature = query.get("msg_signature", "")
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")
        try:
            root = ET.fromstring(body)
            encrypt = root.findtext("Encrypt", default="")
            plain = self.crypt.decrypt(msg_signature, timestamp, nonce, encrypt)
            return _xml_to_dict(plain.decode("utf-8"))
        except Exception:
            return None
