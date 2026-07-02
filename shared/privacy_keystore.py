"""Compatibility shim for the shared Helto privacy keystore package."""

from __future__ import annotations

try:
    from helto_privacy import keystore as _privacy_keystore_backend
    from helto_privacy.keystore import *  # noqa: F401,F403
    from helto_privacy.keystore import PrivacyKeystoreError
except ImportError:
    from . import _vendored_keystore as _privacy_keystore_backend
    from ._vendored_keystore import *  # noqa: F401,F403
    from ._vendored_keystore import PrivacyKeystoreError


def __getattr__(name):
    return getattr(_privacy_keystore_backend, name)
