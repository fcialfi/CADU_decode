"""Time-limited licence for the packaged executable.

A licence is a small JSON file, ``license.key``, placed next to the
executable. It names the customer and an expiry date, and carries an Ed25519
signature over those fields made with a private key that never leaves the
issuer (see license_tool.py). The executable only contains the matching
*public* key, so it can check a licence but cannot create one, and any edit
to the file (e.g. a later expiry date) breaks the signature.

Licensing is active only when ``assets/license_public_key.pem`` exists, i.e.
in builds made after running ``license_tool.py keygen``. Running from source
(``python cadu_gui.py``) never blocks.

To make setting the system clock back less useful, the latest date seen is
remembered in the user's profile; a clock more than a day behind it is
rejected.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import json
import os
import sys
from typing import Optional

PRODUCT = "CADU Decoder"
LICENSE_FILENAME = "license.key"
PUBLIC_KEY_FILENAME = "license_public_key.pem"
WARN_DAYS = 30            # warn in the GUI when the licence expires within this many days
CLOCK_TOLERANCE_DAYS = 1  # allowed clock step back (time zones, manual corrections)


@dataclasses.dataclass
class LicenseStatus:
    valid: bool
    message: str
    enforced: bool = True
    customer: str = ""
    expires: Optional[datetime.date] = None
    days_left: Optional[int] = None

    @property
    def expiring_soon(self) -> bool:
        return self.valid and self.days_left is not None and self.days_left <= WARN_DAYS

    def summary(self) -> str:
        """One line for the GUI ("Licensed to ... · valid until ..."), empty when not enforced."""
        if not self.enforced or not self.valid or self.expires is None:
            return ""
        text = f"Licensed to {self.customer} · valid until {self.expires:%d %B %Y}"
        if self.expiring_soon:
            text += f" ({self.days_left} days left)"
        return text


# --------------------------------------------------------------------------
# Signing and verification
# --------------------------------------------------------------------------
def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_license(private_key, customer: str, expires: datetime.date,
                 issued: Optional[datetime.date] = None) -> dict:
    """Licence dict (ready to be written as JSON) signed with an Ed25519 private key."""
    payload = {
        "product": PRODUCT,
        "customer": customer,
        "issued": (issued or datetime.date.today()).isoformat(),
        "expires": expires.isoformat(),
    }
    signature = private_key.sign(_canonical(payload))
    return {**payload, "signature": base64.b64encode(signature).decode("ascii")}


def verify_license(data: dict, public_key, today: datetime.date) -> LicenseStatus:
    """Check the signature, product and expiry date of a licence dict."""
    from cryptography.exceptions import InvalidSignature

    try:
        payload = {k: data[k] for k in ("product", "customer", "issued", "expires")}
        signature = base64.b64decode(data["signature"], validate=True)
        expires = datetime.date.fromisoformat(payload["expires"])
    except (KeyError, TypeError, ValueError):
        return LicenseStatus(False, "The licence file is damaged or incomplete.")
    try:
        public_key.verify(signature, _canonical(payload))
    except InvalidSignature:
        return LicenseStatus(False, "The licence file is not valid (signature check failed).")
    if payload["product"] != PRODUCT:
        return LicenseStatus(False, f"The licence is for another product ({payload['product']}).")
    days_left = (expires - today).days
    status = LicenseStatus(True, "", customer=payload["customer"], expires=expires, days_left=days_left)
    if days_left < 0:
        status.valid = False
        status.message = f"The licence expired on {expires.isoformat()}."
    else:
        status.message = f"Licensed to {status.customer}, valid until {expires.isoformat()}."
    return status


def load_public_key(path: str):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    with open(path, "rb") as f:
        return load_pem_public_key(f.read())


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _bundle_dir() -> str:
    """Where bundled files (assets/) are: PyInstaller's extraction folder, or this file's folder."""
    if is_frozen():
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def default_license_path() -> str:
    """license.key next to the executable (or next to this file when running from source)."""
    base = os.path.dirname(os.path.abspath(sys.executable)) if is_frozen() else _bundle_dir()
    return os.path.join(base, LICENSE_FILENAME)


def default_public_key_path() -> str:
    return os.path.join(_bundle_dir(), "assets", PUBLIC_KEY_FILENAME)


def default_state_path() -> str:
    """Per-user file remembering the latest date seen (clock rollback check)."""
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(root, "CADU-Decoder", "license_state.json")


# --------------------------------------------------------------------------
# Clock rollback check
# --------------------------------------------------------------------------
def _clock_ok(today: datetime.date, state_path: str) -> bool:
    """False if the clock is well behind the latest date seen; records today otherwise."""
    last_seen = None
    try:
        with open(state_path, encoding="utf-8") as f:
            last_seen = datetime.date.fromisoformat(json.load(f)["last_seen"])
    except (OSError, ValueError, KeyError, TypeError):
        pass  # first run, or unreadable state: nothing to compare against
    if last_seen is not None and (last_seen - today).days > CLOCK_TOLERANCE_DAYS:
        return False
    if last_seen is None or today > last_seen:
        try:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump({"last_seen": today.isoformat()}, f)
        except OSError:
            pass  # a read-only profile must not stop a valid licence
    return True


# --------------------------------------------------------------------------
# Entry point used by the GUI
# --------------------------------------------------------------------------
def check_license(today: Optional[datetime.date] = None,
                  license_path: Optional[str] = None,
                  public_key_path: Optional[str] = None,
                  state_path: Optional[str] = None,
                  enforce_from_source: bool = False) -> LicenseStatus:
    """Licence status of this installation.

    Not enforced (always valid) when the build has no public key, or when
    running from source unless ``enforce_from_source`` is set (tests).
    """
    today = today or datetime.date.today()
    license_path = license_path or default_license_path()
    public_key_path = public_key_path or default_public_key_path()
    state_path = state_path or default_state_path()

    if not os.path.exists(public_key_path):
        return LicenseStatus(True, "Licence check not enabled in this build.", enforced=False)
    if not is_frozen() and not enforce_from_source:
        return LicenseStatus(True, "Licence check skipped (running from source).", enforced=False)

    if not os.path.exists(license_path):
        return LicenseStatus(False, f"No licence found. Place the {LICENSE_FILENAME} file you received "
                                    f"in this folder: {os.path.dirname(license_path)}")
    try:
        with open(license_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return LicenseStatus(False, "The licence file cannot be read.")
    status = verify_license(data, load_public_key(public_key_path), today)
    if status.valid and not _clock_ok(today, state_path):
        status.valid = False
        status.message = "The system date appears to have been set back. Correct the date and restart."
    return status
