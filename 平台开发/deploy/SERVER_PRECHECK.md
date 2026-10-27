# Server precheck

Target: `ops.hhyc-tec.cn`, delivered through Alibaba Cloud ESA and the NAT
mapping `103.236.92.40:30369 -> server port 80`.

Before deployment:

1. Verify the exact release candidate before registration or deployment:
   `python deploy/verify_release_candidate.py --tag <candidate-tag>`.
   The guard accepts only tags listed in `deploy/release-candidates.json`;
   every tag in `historical_tags` must be rejected. This check does not create
   or move a Git tag.
2. Keep the Cloudflare CNAME for `ops.hhyc-tec.cn` pointed at the ESA CNAME.
3. In ESA, keep the enabled origin rule for `ops.hhyc-tec.cn`: HTTP,
   port `30369`, origin Host `ops.hhyc-tec.cn`.
4. Create a separate Baota site for `ops.hhyc-tec.cn`; do not replace another
   existing site. Use its reverse-proxy feature to proxy to `127.0.0.1:5000`.
5. Install the Docker Compose plugin and set the server timezone to
   `Asia/Shanghai`.
6. Keep the existing NAT mappings, including the SSH mapping, unchanged.
7. Before the first start, create the private evidence mount with restrictive
   permissions: `private_uploads/site_photo_originals` under the release
   directory. Compose binds host `./private_uploads` to container
   `/app/private_uploads` and sets `PRIVATE_UPLOAD_DIR=/app/private_uploads`.
   The directory must remain outside `frontend/uploads`; the production process
   performs a read/write probe and exits before database initialization when
   the private directory is missing, read-only, or otherwise unusable.
8. Install `deploy/backup-water-monitor.sh` at
   `/usr/local/sbin/backup-water-monitor` and schedule it daily only after setting
   an explicit non-`/opt` `BACKUP_DIR` and the verified receiver raw/archive host
   path as `INGEST_ARCHIVE_DIR`. The command fails closed when either value is
   missing; it must not infer the historical `backend/data/ingest-archives` path.
   It discovers the host database source from the running container's
   `/app/backend/data` bind mount and requires that source and `BACKUP_DIR` to
   report different filesystem device IDs. The SQLite backup runs in a
   network-disabled temporary container using the active image; its `.part`
   snapshot is created inside `BACKUP_DIR` and renamed there only after
   `PRAGMA quick_check` succeeds. No full snapshot may be staged in the live
   database directory.
   Every successful
   set contains a consistent SQLite snapshot, `frontend/uploads`, and
   `private_uploads/site_photo_originals`; all produced archives are listed in
   the matching `SHA256SUMS-<timestamp>.txt`. A missing or unreadable private
   directory, archive failure, or checksum failure must make the command fail
   without printing `Backup complete`.
9. Before every upgrade, run the backup and verify the checksum manifest with
   `sha256sum -c SHA256SUMS-<timestamp>.txt`. Retention applies to the database,
   public display archive, private original archive, ingest archive,
   and checksum manifest as one timestamped recovery set.
10. Keep Web and the station receiver stopped while the one-shot
    `station-ingest-cutover` Compose dependency runs. It applies migration 014,
    refreshes the bounded startup state and recent slot summaries, verifies
    endpoint/latest-authenticated and receipt-window counts, and writes the durable
    startup marker. A non-zero
    exit blocks both services; do not bypass it or run startup preparation from Web startup.
    The successful JSON output must contain `status=completed`, zero
    `latest_authenticated_mismatches`, and matching expected/actual receipt counts.
    From the immutable release directory, the independent release task runs:
    ```bash
    docker compose --profile station-ingest stop water-monitor station-ingest
    docker compose run --rm station-ingest-cutover
    docker compose run --rm --no-deps station-ingest-cutover \
      python station_ingest_cutover.py verify --database /app/backend/data/water.db
    docker compose --profile station-ingest up -d water-monitor station-ingest
    ```
    Do not run the final `up` command after either one-shot command returns non-zero.
    Re-running `station-ingest-cutover` after a completed marker must refresh the
    bounded startup state again and report `migration=already-applied`; it must not
    reuse an old ready marker without incorporating receipts written during an R25
    rollback interval.
11. Station-ingestion maintenance is now a bounded, resumable operation, but the data
    administrator remains responsible for retention/retirement policy and capacity
    thresholds. Before the first run, obtain explicit approved byte budgets for the
    SQLite hot filesystem and the local raw/archive filesystem. Install the scripts and
    units with `install-station-ingest-retention-timer.sh`; installation deliberately
    does not enable the timer. Run the service manually once, inspect the JSON result and
    database/storage metrics, and only then request approval to enable the six-hour
    timer. `status=partial` with `retry_required=true` is a successful bounded stop and
    resumes on a later run; a failed command or lock conflict is not converted to
    success. Do not pass `--mirror-storage-dir` until a real, readable NAS mount has been
    verified. The currently proven WebDAV path is external transfer evidence, not a
    local mount and not permission to delete local archive files. Index reduction and
    compaction remain excluded.

## Consistent restore

Restore one matching timestamp as a unit; never restore only the database or
only one file tree:

1. Stop the application and keep the current database, public uploads, private
   originals, and receiver raw/archive directory as a rollback copy.
2. Verify every artifact using its `SHA256SUMS-<timestamp>.txt` before extraction.
3. Restore `water.db-<timestamp>` to `backend/data/water.db`, extract
   `uploads-<timestamp>.tar.gz` under `frontend`, and extract
   `private-originals-<timestamp>.tar.gz` under `private_uploads` so the final
   directory is `private_uploads/site_photo_originals`. Extract the matching
   `ingest-archives-<timestamp>.tar.gz` back to the exact parent of the approved
   `INGEST_ARCHIVE_DIR`; do not restore it under a guessed release-tree path.
4. Reapply the release owner's restrictive read/write permissions, then start
   the service. Startup must pass the private-storage probe.
5. Verify an authenticated same-site request can read a known original through
   `/api/attachments/<id>/original`, an anonymous request is rejected, and the
   displayed file still comes from `/uploads`. Compare the restored original's
   SHA-256 with its attachment metadata before declaring recovery complete.

The application container binds only to `127.0.0.1:5000`. Public traffic must
enter through the Baota-managed Nginx site. TLS is terminated by ESA after its
certificate is configured. The real `WX_APPSECRET` belongs in the server-side
`.env` file and must not be committed.
