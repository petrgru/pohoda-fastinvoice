"""Smoke-check the company registries from where the app actually runs.

The public Czech registries are not reachable from every network (and ARES
blocks abuse), so run this once on the cluster / deployment host after
changing any REGISTRY_* setting:

    uv run python scripts/check_registries.py [ICO]

Prints the raw answers plus the parsed RegistryRecord so a schema change in
ARES/ADIS can be spotted and the parser patterns calibrated against real
payloads. Exits non-zero when a probe fails.
"""

# ruff: noqa: T201  (CLI diagnostics print to stdout on purpose)

import json
import sys

from app.services import registry

DEFAULT_ICO = "06846904"  # real VAT payer with a clean mod-11 checksum


def probe_ares(ico: str) -> bool:
    url = registry.ares_request_url(ico)
    print(f"[ARES] GET {url}")
    try:
        provider = registry.AresProvider()
        record = provider.lookup_ico(ico)
    except registry.RegistryUnavailableError as exc:
        print(f"[ARES] UNAVAILABLE: {exc}")
        return False
    print(f"[ARES] parsed: {record}")
    if record is not None:
        raw = json.dumps(record.raw, ensure_ascii=False)
        print(f"[ARES] raw: {raw[:800]}")
    return record is not None


def probe_vies(dic: str) -> bool:
    print(f"[VIES] checkVat {dic}")
    try:
        record = registry.ViesProvider(dic).check()
    except registry.RegistryUnavailableError as exc:
        print(f"[VIES] UNAVAILABLE: {exc}")
        return False
    print(f"[VIES] parsed: {record}")
    return True  # a definitive INVALID answer still proves connectivity


def probe_adis(dic: str) -> bool:
    print(f"[ADIS] bulk check {dic}")
    from app.services import adis

    try:
        results = adis.check_batch([dic])
    except adis.AdisUnavailableError as exc:
        print(f"[ADIS] UNAVAILABLE: {exc}")
        return False
    print(f"[ADIS] parsed: {results}")
    return bool(results)


def main() -> int:
    ico = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ICO
    if not registry.ico_checksum_ok(ico):
        print(f"warning: IČO {ico} fails the mod-11 check (probe continues)")
    ares_ok = probe_ares(ico)
    vies_ok = probe_vies(f"CZ{ico}")
    if not probe_adis(f"CZ{ico}"):
        print("[ADIS] warning: unreliable-payer enrichment stays unavailable")
    return 0 if ares_ok and vies_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
