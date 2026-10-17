"""POST /disclosure/{batchId} authentication: EIP-191 signature over a timestamped message."""
from __future__ import annotations

import re

from eth_utils import is_address, to_checksum_address

from .signing import SignatureError, recover_text_signer

WINDOW_SECONDS = 300
# fullmatch, not match+$: Python's `$` also matches just before a trailing newline.
_MESSAGE_RE = re.compile(r"EmbedProof disclosure\nbatch:(0|[1-9][0-9]{0,17})\nts:(0|[1-9][0-9]{0,11})")


class DisclosureAuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def build_message(batch_id: int, ts: int) -> str:
    return f"EmbedProof disclosure\nbatch:{batch_id}\nts:{ts}"


def authenticate(batch_id: int, address: str, message: str, signature: str, now: float) -> str:
    """Return the checksummed signer if the message is exact, fresh and signed by `address`.

    422 = malformed request, 403 = stale timestamp / wrong signer.
    """
    m = _MESSAGE_RE.fullmatch(message) if isinstance(message, str) else None
    if not m:
        raise DisclosureAuthError(422, "message must be exactly 'EmbedProof disclosure\\nbatch:<id>\\nts:<unix>'")
    if int(m.group(1)) != batch_id:
        raise DisclosureAuthError(403, "message batch id does not match the requested batch")
    if abs(now - int(m.group(2))) > WINDOW_SECONDS:
        raise DisclosureAuthError(403, f"message timestamp outside the +-{WINDOW_SECONDS} s window")
    if not (isinstance(address, str) and is_address(address)):
        raise DisclosureAuthError(422, "address is not a valid EVM address")
    try:
        signer = recover_text_signer(message, signature)
    except SignatureError as exc:
        raise DisclosureAuthError(422, str(exc)) from exc
    if signer != to_checksum_address(address):
        raise DisclosureAuthError(403, "signature was not produced by the stated address")
    return signer
