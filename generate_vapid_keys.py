#!/usr/bin/env python3
"""
Run this once locally to generate your VAPID key pair.
Save the output — you'll need both keys as Railway env vars.

  pip install pywebpush
  python generate_vapid_keys.py
"""

import base64
from py_vapid import Vapid
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat
)

vapid = Vapid()
vapid.generate_keys()

# Export private key as DER base64url — the format py_vapid's from_string()
# expects. Do NOT use private_pem() here; PEM format contains newlines that
# get mangled when stored as a Railway environment variable, silently breaking
# push notifications.
der_bytes = vapid.private_key.private_bytes(
    Encoding.DER, PrivateFormat.PKCS8, NoEncryption()
)
private_key_b64 = base64.urlsafe_b64encode(der_bytes).decode().rstrip("=")

print("=" * 60)
print("VAPID Keys — save these as Railway environment variables")
print("=" * 60)
print(f"\nVAPID_PRIVATE_KEY={private_key_b64}")
print(f"\nVAPID_PUBLIC_KEY={vapid.public_key}")
print("\nAlso add to Railway:")
print("VAPID_EMAIL=mailto:stephengaffney7@gmail.com")
print("SUPABASE_URL=https://rhqyfjikjkwrzzhttuwq.supabase.co")
print("SUPABASE_SERVICE_KEY=<your service role key>")
print("WEBHOOK_SECRET=gyard_secret_2026")
print()
print("NOTE: VAPID_PRIVATE_KEY is now in DER/base64url format (single line,")
print("no headers). This is required — PEM format breaks in Railway env vars.")
