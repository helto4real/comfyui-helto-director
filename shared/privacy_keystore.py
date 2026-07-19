"""Compatibility shim for the shared Helto privacy keystore package."""

from __future__ import annotations

from helto_privacy import keystore as _privacy_keystore_backend
from helto_privacy.keystore import *  # noqa: F401,F403
from helto_privacy.keystore import PrivacyKeystoreError


def __getattr__(name):
    return getattr(_privacy_keystore_backend, name)
