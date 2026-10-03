# TREND

Private source repository for the TREND VPS edition of PULSE (TOPTOP and LERA NENA).

The source tree is synchronized from the running TREND reader container. The current image and per-file SHA-256 hashes are in `LIVE_SOURCE_MANIFEST.json`. `SOURCE_MANIFEST.json` records the original September 18 import and is historical.

The VPS checks code every 30 seconds after the previous check finishes and requires two matching snapshots before committing. Changes normally reach GitHub within approximately 1–2 minutes, depending on snapshot and network duration. No changes means no new commit. Synchronization survives VPS reboot and does not require this laptop or Codex to remain open.

Included: Python application and selected scripts, SQL migrations, compiled frontend assets, Dockerfile and requirements. Excluded: credentials, databases, runtime data, agent memory, unrelated client scripts. Editable frontend source is not present in the deployed image. This repository is not a database backup.

Synchronization is one-way from the running VPS code to GitHub. Local drafts are not automatically deployed or uploaded. The source service does not deploy code, restart TREND, change databases, or force-push. Concurrent edits of managed source files on GitHub stop synchronization for manual reconciliation.

Operator commands on VPS:

```sh
systemctl status trend-source-sync.timer
journalctl -u trend-source-sync.service -n 30 --no-pager
sudo systemctl stop trend-source-sync.timer
```

Implementation: `tools/trend_sync/`. Server mirror and dedicated GitHub deploy key are outside the production workspace in `/home/egor/trend-github-sync`; the private key is never committed.
