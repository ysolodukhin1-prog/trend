# Live source synchronization

Prepared 2026-10-03 from running container `toptop_data_stage-pulse_reader-1`, image `toptop-trend:lamoda-combined-r177-20261001`.

The live image and hashes are recorded in LIVE_SOURCE_MANIFEST.json on each source update. The historical SOURCE_MANIFEST.json is not the current version inventory. Source includes the mounted data_access.py as visible inside the running container. No database or credential files are included.

Python source is parsed before every commit. File exclusions, secret-pattern guards, archive path/checksum validation, stable-snapshot debounce and concurrent Git edit protection are tested separately. These checks do not establish application integration correctness. Production services are not modified by synchronization.

The deployed frontend includes compiled assets and historical asset versions retained in the image, not its editable React source tree. The Dockerfile captured from the image is archival; image layers and separately provisioned services/secrets mean a standalone rebuild is not asserted to reproduce the running deployment. The repository tracks selected deployed source, not all server state.
