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
   `/usr/local/sbin/backup-water-monitor` and schedule it daily. Every successful
   set contains a consistent SQLite snapshot, `frontend/uploads`, and
   `private_uploads/site_photo_originals`; all produced archives are listed in
   the matching `SHA256SUMS-<timestamp>.txt`. A missing or unreadable private
   directory, archive failure, or checksum failure must make the command fail
   without printing `Backup complete`.
9. Before every upgrade, run the backup and verify the checksum manifest with
   `sha256sum -c SHA256SUMS-<timestamp>.txt`. Retention applies to the database,
   public display archive, private original archive, optional ingest archive,
   and checksum manifest as one timestamped recovery set.

## Consistent restore

Restore one matching timestamp as a unit; never restore only the database or
only one file tree:

1. Stop the application and keep the current database, public uploads, and
   private originals as a rollback copy.
2. Verify every artifact using its `SHA256SUMS-<timestamp>.txt` before extraction.
3. Restore `water.db-<timestamp>` to `backend/data/water.db`, extract
   `uploads-<timestamp>.tar.gz` under `frontend`, and extract
   `private-originals-<timestamp>.tar.gz` under `private_uploads` so the final
   directory is `private_uploads/site_photo_originals`.
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
