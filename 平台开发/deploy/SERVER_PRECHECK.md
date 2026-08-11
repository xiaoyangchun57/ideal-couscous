# Server precheck

Target: `ops.hhyc-tec.cn`, delivered through Alibaba Cloud ESA and the NAT
mapping `103.236.92.40:30369 -> server port 80`.

Before deployment:

1. Verify the exact release candidate before registration or deployment:
   `python deploy/verify_release_candidate.py --tag <candidate-tag>`.
   The guard accepts only tags listed in `deploy/release-candidates.json`;
   r1/r2/r3 are historical and must be rejected. This check does not create
   or move a Git tag.
2. Keep the Cloudflare CNAME for `ops.hhyc-tec.cn` pointed at the ESA CNAME.
3. In ESA, keep the enabled origin rule for `ops.hhyc-tec.cn`: HTTP,
   port `30369`, origin Host `ops.hhyc-tec.cn`.
4. Create a separate Baota site for `ops.hhyc-tec.cn`; do not replace another
   existing site. Use its reverse-proxy feature to proxy to `127.0.0.1:5000`.
5. Install the Docker Compose plugin and set the server timezone to
   `Asia/Shanghai`.
6. Keep the existing NAT mappings, including the SSH mapping, unchanged.
7. Install `deploy/backup-water-monitor.sh` at
   `/usr/local/sbin/backup-water-monitor` and schedule it daily. It creates a
   consistent SQLite snapshot plus a compressed copy of `frontend/uploads`.
8. Back up `backend/data/water.db` and `frontend/uploads` before every upgrade.

The application container binds only to `127.0.0.1:5000`. Public traffic must
enter through the Baota-managed Nginx site. TLS is terminated by ESA after its
certificate is configured. The real `WX_APPSECRET` belongs in the server-side
`.env` file and must not be committed.
