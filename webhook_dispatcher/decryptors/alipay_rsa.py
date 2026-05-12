"""Alipay RSA2 callback signature verification and parsing."""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .base import BaseDecryptor

logger = logging.getLogger(__name__)


class AlipayRSADecryptor(BaseDecryptor):
    """Alipay callback verifier using RSA2 (SHA256withRSA) signature."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        key_pem = config.get("alipay_public_key", "")
        if not key_pem or key_pem.startswith("MIIBIjANBg..."):
            self.public_key = None
            logger.warning("[alipay] alipay_public_key is placeholder, signature verification disabled")
            return
        if "-----BEGIN" not in key_pem:
            key_pem = f"-----BEGIN PUBLIC KEY-----\n{key_pem}\n-----END PUBLIC KEY-----"
        self.public_key = serialization.load_pem_public_key(key_pem.encode())
        self.charset = config.get("charset", "UTF-8")
        self.sign_type = config.get("sign_type", "RSA2")

    def verify(self, query: Dict[str, str]) -> Optional[str]:
        return "success"

    def decrypt(self, body: bytes, query: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Alipay sends form-encoded params with sign and sign_type.
        Verify signature (if key available) and return parsed params."""
        try:
            parsed = parse_qs(body.decode(self.charset if self.public_key else "UTF-8"),
                              keep_blank_values=True)
            params = {k: v[0] for k, v in parsed.items()}
            sign = params.pop("sign", "")
            params.pop("sign_type", "")

            if self.public_key and sign:
                sign_content = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
                hash_alg = hashes.SHA256() if self.sign_type == "RSA2" else hashes.SHA1()
                self.public_key.verify(
                    base64.b64decode(sign),
                    sign_content.encode(self.charset),
                    padding.PKCS1v15(),
                    hash_alg,
                )
            return params
        except Exception as e:
            logger.warning(f"[alipay] decrypt/verify failed: {e}")
            return None
