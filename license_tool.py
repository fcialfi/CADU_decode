"""Create and check licences for the packaged executable (see licensing.py).

  1. Once: create the key pair.
       python license_tool.py keygen --private-key C:\\secure\\cadu_decoder_license_private_key.pem
     The private key stays with the issuer (keep it outside the repository);
     the public key goes to assets/license_public_key.pem, which enables the
     licence check in every executable built from then on.

  2. For each customer: issue a licence file and send it with the executable.
       python license_tool.py issue --private-key C:\\secure\\cadu_decoder_license_private_key.pem ^
           --customer "Customer name" --expires 2027-12-31 -o license.key

  3. Optional: check a licence file.
       python license_tool.py verify license.key
"""

import argparse
import datetime
import json
import os
import sys

import licensing

DEFAULT_PUBLIC_KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets",
                                  licensing.PUBLIC_KEY_FILENAME)


def keygen(args) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    for path in (args.private_key, args.public_key):
        if os.path.exists(path) and not args.force:
            print(f"ERROR: {path} already exists. Licences issued with the old key would stop "
                  "working in new builds; use --force only if that is intended.", file=sys.stderr)
            return 1
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    for path in (args.private_key, args.public_key):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(args.private_key, "wb") as f:
        f.write(private_pem)
    with open(args.public_key, "wb") as f:
        f.write(public_pem)
    print(f"Private key: {args.private_key}  (keep it secret, never commit it)")
    print(f"Public key:  {args.public_key}  (commit it; it is bundled into the executable)")
    return 0


def issue(args) -> int:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        expires = datetime.date.fromisoformat(args.expires)
    except ValueError:
        print("ERROR: --expires must be a date as YYYY-MM-DD", file=sys.stderr)
        return 1
    if not os.path.isfile(args.private_key):
        print(f"ERROR: private key not found: {args.private_key}\n"
              "Check the path (the one given to keygen), or create the key pair first with:\n"
              "  python license_tool.py keygen --private-key <path of the private key>", file=sys.stderr)
        return 1
    with open(args.private_key, "rb") as f:
        key = load_pem_private_key(f.read(), password=None)
    lic = licensing.sign_license(key, args.customer, expires)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(lic, f, indent=2)
    print(f"Licence for {args.customer!r}, valid until {expires.isoformat()}: {args.output}")
    return 0


def verify(args) -> int:
    for label, path in (("licence file", args.license), ("public key", args.public_key)):
        if not os.path.isfile(path):
            print(f"ERROR: {label} not found: {path}", file=sys.stderr)
            return 1
    with open(args.license, encoding="utf-8") as f:
        data = json.load(f)
    status = licensing.verify_license(data, licensing.load_public_key(args.public_key),
                                      datetime.date.today())
    print(("VALID: " if status.valid else "INVALID: ") + status.message)
    return 0 if status.valid else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    k = sub.add_parser("keygen", help="create the signing key pair (once)")
    k.add_argument("--private-key", required=True, help="where to write the private key (outside the repository)")
    k.add_argument("--public-key", default=DEFAULT_PUBLIC_KEY, help="where to write the public key")
    k.add_argument("--force", action="store_true", help="overwrite existing keys")
    k.set_defaults(func=keygen)

    i = sub.add_parser("issue", help="issue a licence file")
    i.add_argument("--private-key", required=True)
    i.add_argument("--customer", required=True)
    i.add_argument("--expires", required=True, help="last valid day, YYYY-MM-DD")
    i.add_argument("-o", "--output", default=licensing.LICENSE_FILENAME)
    i.set_defaults(func=issue)

    v = sub.add_parser("verify", help="check a licence file")
    v.add_argument("license")
    v.add_argument("--public-key", default=DEFAULT_PUBLIC_KEY)
    v.set_defaults(func=verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
