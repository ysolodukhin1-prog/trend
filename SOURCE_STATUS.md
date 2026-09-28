# TREND source snapshot

TREND is the VPS edition of PULSE for TOPTOP and LERA NENA.

This source tree comes from the deployed-source audit captured on 2026-09-18. It is not asserted to equal the current VPS release: SSH on 2026-09-28 timed out during banner exchange. Later deployment patches remain available locally for reconciliation. No production changes were made.

Secrets, environment files, database dumps, runtime logs, agent memory and unrelated client analysis scripts are excluded. Runtime requires separately configured databases and credentials. See SOURCE_MANIFEST.json for per-file provenance.

## Known limitations

- The referenced stylesheet was restored from deployment/seo-yandex-20260918; its SHA-256 matches the r41-registry-db copy.
- Later deployment revisions (through r154) are not represented by this snapshot.
- Editable frontend sources from the shared PULSE checkout include unrelated client proposals and are not imported. The captured runtime JavaScript bundle is included.
- Python source syntax was checked without executing application code; runtime integration has not been tested.
