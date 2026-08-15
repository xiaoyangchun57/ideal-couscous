# r7 final release gate (2026-08-15)

Target candidate tag: `release-20260815-cross-module-freeze-r7`

The product-approved development workspace has passed the final automated gate for r7. This document records release readiness evidence only; it does not claim that an immutable commit, annotated tag, source package, push, or deployment already exists.

## Final automated gate

| Check | Result | Exit |
| --- | --- | ---: |
| Backend unittest discover | 310/310 passed | 0 |
| Miniprogram Node tests | 32/32 passed | 0 |
| React `test:api` | 41/41 passed | 0 |
| React cockpit tests | 2/2 passed | 0 |
| React lint | PASS | 0 |
| React production build | PASS; 1741 modules transformed | 0 |
| Python/JavaScript syntax aggregate | 136 files passed | 0 |
| Backup script tests | 3/3 passed | 0 |
| Candidate guard | 2/2 passed; r1-r6 rejected and r7 accepted | 0 |
| `git diff --check` | No whitespace errors; LF-to-CRLF warnings only | 0 |

The backend logs for `TEST_MEDIA_FIX_notification_failure` and the work-order attachment flagging HTTP 500 are intentional failure-injection paths. Their rollback assertions passed and the full backend suite completed successfully.

## Product UI status

Current-candidate business UI: **NOT RUN**.

Product explicitly authorized skipping the remaining real UI regression and proceeding to release preparation so that actual users can test after deployment. Historical UI results are not reused as r7 evidence. Residual product risk remains in:

- miniprogram return-to-site rework and evidence resubmission;
- miniprogram login and notification-badge refresh;
- the seven Web correction groups on the complete r7 candidate.

These boundaries must be monitored through the limited online smoke check and actual-user feedback; they are not recorded as PASS here.

## Candidate guard and identity

`deploy/release-candidates.json` accepts only `release-20260815-cross-module-freeze-r7`. All entries in `historical_tags`, covering r1 through r6, must be rejected and must not be moved, reused, or deployed as the current candidate.

The immutable commit, annotated r7 tag, and tag-derived source package will be generated together only after this document is included in the candidate commit. Their commit IDs, tag object and peeled commit, package path, size, and SHA256 must be recorded from the generated artifacts; no unknown values are asserted in advance.

## Production read-only precheck

- `https://ops.hhyc-tec.cn/api/health` returned HTTP 200.
- The current r6 container is running and healthy.
- The TLS certificate is valid through 2026-10-28.
- The filesystem containing `/opt` has 9.6 GB available.
- The current production release record is r6.

This was a read-only precheck. r7 has not been deployed.

## Backup blocker

`water-monitor-backup.timer` failed on August 13, 14, and 15 because the installed script used the stale default `/opt/water-monitor-current` while the running Compose project used a different release working directory. The repository backup script now discovers and validates the active Compose working directory when `APP_DIR` is not explicitly supplied; its 3/3 isolated tests passed.

Before deployment:

1. Install the corrected backup script from the candidate.
2. Produce a fresh database snapshot and uploads archive.
3. Verify the generated SHA256 values and confirm the backup artifacts are readable.
4. Stop the deployment immediately if script installation, backup creation, or verification fails.

The prior failed timer runs are not valid deployment backups.

## Package and rollback boundary

The source package must be generated from the annotated r7 tag and contain committed source only. It must exclude private IDE configuration, isolated databases and uploads, UI evidence, automation caches or scripts, logs, temporary fixtures, and other untracked artifacts.

Keep the r6 release directory, image, and verified pre-r7 data backup available throughout deployment. If r7 health checks, database integrity checks, or the public smoke test fails, immediately return to r6 and restore data only when the failure requires it.

No push, deployment, online database write, or production container change was performed by this release gate.
