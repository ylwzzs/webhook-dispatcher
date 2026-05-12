from .wecom_aes import WeComAESDecryptor
from .alipay_rsa import AlipayRSADecryptor

DECRYPTORS = {
    "wecom_aes": WeComAESDecryptor,
    "alipay_rsa": AlipayRSADecryptor,
}

__all__ = ["DECRYPTORS", "WeComAESDecryptor", "AlipayRSADecryptor"]
