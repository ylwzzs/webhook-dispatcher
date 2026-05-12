"""Base decryptor interface for webhook callbacks."""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseDecryptor(ABC):
    """Abstract base class for platform-specific callback decryptors."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def verify(self, query: Dict[str, str]) -> Optional[str]:
        """Handle GET verification request (e.g., WeCom URL verification).
        Returns the plaintext echo string on success, None on failure."""
        return None

    @abstractmethod
    def decrypt(self, body: bytes, query: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Decrypt and parse a POST callback body.
        Returns parsed dict on success, None on failure."""
        return None
