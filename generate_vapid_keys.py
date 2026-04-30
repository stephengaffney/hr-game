#!/usr/bin/env python3
"""
Run this once locally to generate your VAPID key pair.
Save the output — you'll need both keys as Railway env vars.

  pip install pywebpush
  python generate_vapid_keys.py
"""

from py_vapid import Vapid

vapid = Vapid()
vapid.generate_keys()

private_key = vapid.private_key
public_key  = vapid.public_key

print("=" * 60)
print("VAPID Keys — save these as Railway environment variables")
print("=" * 60)
print(f"\nVAPID_PRIVATE_KEY={vapid.private_pem().decode().strip()}")
print(f"\nVAPID_PUBLIC_KEY={vapid.public_key}")
print("\nAlso add to Railway:")
print("VAPID_EMAIL=mailto:stephengaffney7@gmail.com")
print("SUPABASE_URL=https://rhqyfjikjkwrzzhttuwq.supabase.co")
print("SUPABASE_SERVICE_KEY=<your service role key>")
print("WEBHOOK_SECRET=gyard_secret_2026")
