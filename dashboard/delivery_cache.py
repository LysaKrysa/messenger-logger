"""Chunk-authenticated, encrypted resend payloads; never store plaintext files."""
from contextlib import contextmanager
import base64
import json
import os
import re
import stat
import struct
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from .store import DashboardError

BLOCK = 256 * 1024
MAGIC = b"MLR1\n"


class PayloadUnavailable(DashboardError):
    pass


class DeliveryCache:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "delivery-cache"
        if self.root.is_symlink():
            raise DashboardError("Resend cache must not be a symbolic link.")
        self.root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)

    def path(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{32}", ident):
            raise PayloadUnavailable("Resend content is unavailable.")
        return self.root / (ident + ".cache")

    def cipher(self, account, ident):
        return Fernet(base64.urlsafe_b64encode(bytes.fromhex(self.store.secret("delivery-cache:" + account + ":" + ident))))

    def write(self, account, ident, metadata, source=None):
        cipher = self.cipher(account, ident)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix="." + ident + ".", suffix=".tmp", delete=False) as output:
                temporary = output.name
                os.fchmod(output.fileno(), 0o600)
                output.write(MAGIC)
                def frame(index, data):
                    token = cipher.encrypt(struct.pack(">Q", index) + data)
                    output.write(struct.pack(">I", len(token)))
                    output.write(token)
                frame(0, json.dumps(metadata, ensure_ascii=True).encode())
                index, total = 1, 0
                if source is not None:
                    source.seek(0)
                    while block := source.read(BLOCK):
                        total += len(block)
                        if total > 50_000_000:
                            raise PayloadUnavailable("Resend payload exceeds its limit.")
                        frame(index, block)
                        index += 1
                if total != metadata["file_size"]:
                    raise PayloadUnavailable("Resend payload changed during storage.")
                frame(index, b"")
                output.flush()
                os.fsync(output.fileno())
                size = output.tell()
            os.replace(temporary, self.path(ident))
            temporary = None
            return size
        except (OSError, ValueError):
            raise PayloadUnavailable("Resend content could not be stored.") from None
        finally:
            if temporary is not None:
                try: os.unlink(temporary)
                except FileNotFoundError: pass
            if source is not None:
                source.seek(0)

    @contextmanager
    def read(self, account, ident):
        cipher = self.cipher(account, ident)
        yielded = False
        try:
            descriptor = os.open(self.path(ident), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError
                def frame(index):
                    count = source.read(4)
                    if len(count) != 4:
                        raise ValueError
                    size = struct.unpack(">I", count)[0]
                    if not 80 <= size <= 2 * 1024 * 1024:
                        raise ValueError
                    token = source.read(size)
                    if len(token) != size:
                        raise ValueError
                    plain = cipher.decrypt(token)
                    if len(plain) < 8 or struct.unpack(">Q", plain[:8])[0] != index:
                        raise ValueError
                    return plain[8:]
                def metadata():
                    if source.read(len(MAGIC)) != MAGIC:
                        raise ValueError
                    value = json.loads(frame(0))
                    if (not isinstance(value, dict) or value.get("version") != 1 or value.get("method") not in {"sendMessage", "sendDocument"}
                            or not isinstance(value.get("fields"), dict) or type(value.get("file_size")) is not int
                            or not 0 <= value["file_size"] <= 50_000_000):
                        raise ValueError
                    return value
                meta = metadata()
                def blocks():
                    index, total = 1, 0
                    while True:
                        block = frame(index)
                        if not block:
                            if total != meta["file_size"] or source.read(1):
                                raise ValueError
                            return
                        total += len(block)
                        if total > meta["file_size"]:
                            raise ValueError
                        yield block
                        index += 1
                # Authenticate every chunk, order and final marker before any
                # upstream call. Rewind the same descriptor for bounded streaming.
                for _ in blocks():
                    pass
                source.seek(0)
                metadata()
                yielded = True
                yield meta, blocks
        except (OSError, ValueError, KeyError, TypeError, InvalidToken):
            if yielded:
                raise
            raise PayloadUnavailable("Resend content is unavailable or failed its integrity check.") from None
