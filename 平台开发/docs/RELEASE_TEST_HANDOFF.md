# r12 frozen candidate gate (2026-09-07)

Target candidate tag: `release-20260907-cross-module-freeze-r12`

r12 is the only candidate accepted by `deploy/release-candidates.json`; r1 through r11 are immutable historical tags and remain rejected. The candidate contains the post-r11 WeChat approval subscription delivery closure: server-owned templates, persistent outbox delivery, cycle-safe audit deep links, applicant result destinations, result-read synchronization, and direct regression coverage. All automated checks used isolated databases, temporary uploads and test configuration; no fixed service, real WeChat API or real business data was used.

## r12 final automated evidence

| Check | Result | Time | Exit |
| --- | --- | --- | ---: |
| Backend unittest discover | 521/521 passed | 11:01:32–11:03:45 | 0 |
| Miniprogram Node tests | 211/211 passed across all 39 `*.test.js` files | 11:04:09 | 0 |
| Combined syntax gate | 164 JavaScript/Python files passed | 11:04:46–11:04:57 | 0 |
| React Node tests | 93/93 passed across all 15 `src/**/*.test.js` files | 11:04:09–11:04:10 | 0 |
| React ESLint | PASS | 11:04:47–11:05:06 | 0 |
| React production build | PASS; 1746 modules transformed | 11:05:27–11:05:37 | 0 |
| Backup archive and candidate guard unittest | 6/6 passed; archived backup script retains LF | 11:05:27–11:05:32 | 0 |
| Direct candidate verification | r12 accepted | 11:05:32 | 0 |
| Cached diff and boundary checks | No whitespace errors, no private candidate files and no half-staged critical paths | final freeze check | 0 |

## r12 product and residual evidence boundary

The candidate preserves the accepted r11 real-UI evidence. The WeChat approval delivery changes have automated coverage only; actual WeChat subscription, binding and physical-device delivery are **NOT RUN** and require two real accounts after product Review.

The following residual boundaries remain unchanged:

- Responsible sites to site archive redesign: **DEFERRED**.
- Review return-context verification after remediation blocking: **NOT RUN**.
- Complete real-data chain after vehicle checkout, including vehicle-use record and arrival gate: **NOT RUN**.
- Specified real-object checks for plan #46 continuation and plan #43 remediation continuation: **NOT RUN**.
- Scenarios without natural data and DOCX/XLSX download visuals: retain recorded **NOT RUN** / **SKIPPED** status.

No package, push, server connection, deployment, fixed-service operation or real-database operation is part of this freeze evidence.

---

# r11 frozen candidate gate (2026-09-05)

Target candidate tag: `release-20260905-cross-module-freeze-r11`

r11 is the only candidate accepted by `deploy/release-candidates.json`; r1 through r10 are immutable historical tags and remain rejected. The gate used only isolated test databases, temporary uploads and test configuration. It did not start or reuse the fixed local service and did not touch real business data.

## r11 final automated evidence

| Check | Result | Time | Exit |
| --- | --- | --- | ---: |
| Backend unittest discover | 509/509 passed | 11:20:18–11:22:37 | 0 |
| Miniprogram Node tests | 209/209 passed across all 39 `*.test.js` files | 11:22:46 | 0 |
| Combined syntax gate | 163 JavaScript/Python files passed | 11:23:04–11:23:15 | 0 |
| React Node tests | 93/93 passed across all 15 `src/**/*.test.js` files | 11:23:05–11:23:06 | 0 |
| React ESLint | PASS | 11:23:24–11:23:50 | 0 |
| React production build | PASS; 1746 modules transformed | 11:23:57–11:24:08 | 0 |
| Backup archive and candidate guard unittest | 6/6 passed; archived backup script retains LF | 11:24:28–11:24:32 | 0 |
| Direct candidate verification | r11 accepted; r10 rejected as historical | 11:24:32 | 0 |
| Cached diff and boundary checks | No whitespace errors, no private candidate files and no half-staged critical paths | final freeze check | 0 |

## r11 first-run failures and permitted test-only corrections

The first complete backend run (11:05:13–11:07:28) ran 509 tests and failed with 2 failures and 16 errors. No production defect was found. Seven isolated test fixtures were behind already-reviewed contracts: one new test leaked Flask `TESTING` state; evidence-history called the current row-based helper with a legacy integer; two replacement-photo fixtures omitted the current submitted photo path; and minimal audit, vehicle, notification and work-order databases lacked current columns or empty support tables. Only those tests were corrected. The next complete backend run passed 509/509.

The first all-file miniprogram run (11:18:52–11:18:53) ran 209 tests and had 3 stale assertions: the reviewed inspection feedback typography is 30rpx/24rpx, exact remediation navigation carries `reworkOnly: true`, and an approved plan without an explicit execution status safely projects as waiting for field execution. Only the three tests were updated. Because the candidate changed, the complete gate was restarted from backend discovery; the final results are the table above. Intentional fault-injection 500 traces remained visible while their rollback assertions passed.

## r11 UI and product evidence boundary

Previously accepted real-UI results remain **PASS** only for their recorded scopes: plan editing; common plans, field reporting, work-order and alert lists/details; reachability of the six vehicle sheets and vehicle inspection items; message/mine icons, empty states and subscription action; and review-photo grouping and its accepted visual rework.

The following remain exactly as authorized and are not promoted by this automated gate:

- Responsible sites to site archive redesign: **DEFERRED**; current implementation is frozen without executing the deferred handoff.
- Review return-context verification after remediation blocking: **NOT RUN**.
- Complete real-data chain after vehicle checkout, including vehicle-use record and arrival gate: **NOT RUN**.
- Specified real-object checks for plan #46 continuation and plan #43 remediation continuation: **NOT RUN**.
- Scenarios without natural data and DOCX/XLSX download visuals: retain their recorded **NOT RUN** / **SKIPPED** status.

No package, push, server connection, deployment, fixed-service operation or real-database operation is part of this freeze evidence.

---

# r10 candidate gate (2026-08-19)

Target candidate tag: `release-20260819-cross-module-freeze-r10`

r10 is the only candidate accepted by the release manifest; r1 through r9 are immutable historical tags and remain rejected. The complete automated gate, precise 60-file staging, cached diff check, candidate guard, and Git index boundary check have passed. A frozen candidate content boundary now exists, but no immutable commit or tag has been created.

## r10 automated evidence

| Check | Result | Exit |
| --- | --- | ---: |
| Backend unittest discover | 365/365 passed in 83.068s | 0 |
| Miniprogram Node tests | 51/51 passed across 23 test files | 0 |
| Miniprogram JavaScript syntax | 62/62 files passed `node --check` | 0 |
| React tests | 71/71 passed across 13 test files | 0 |
| React ESLint | PASS | 0 |
| React production build | PASS; 1748 modules transformed | 0 |
| Backend Python syntax | 75/75 files passed | 0 |
| `python-docx` import smoke | PASS; version 1.2.0 | 0 |
| Candidate guard | Product-independent 2/2 passed; r1-r9 rejected and r10 accepted | 0 |
| Working-tree diff check | No whitespace errors; existing LF-to-CRLF notices only | 0 |

The first complete backend run had two failures, both caused by stale test contracts: the multirole in-memory fixture lacked the production-required `insp_plans` and `insp_plan_items` tables, and an old period-shortening test incorrectly expected business dates and vehicle assignments to be silently pruned. Only tests were corrected. The three directly affected modules then passed 64/64 in development and 3/3 in independent product verification; the final complete backend run passed 365/365. No business code was changed for these gate failures. HTTP 500 logs from attachment voiding and work-order image upload are intentional fault-injection paths whose assertions passed.

## r10 UI and freeze boundary

Previously recorded real-UI PASS results for the changed scope remain valid. The three invalid-scheduler reason cards in cleanup remain **NOT RUN** because the real database has no natural candidate and no data was fabricated. Download entry points and DOCX/XLSX visual checks remain **SKIPPED**. These boundaries do not establish that all eight areas passed UI review and do not authorize deployment.

The final candidate boundary is 60 files: 52 tracked content diffs and 8 required untracked implementation/test files. The index check found exactly 60 cached paths, all under `平台开发`, with zero forbidden paths and zero unstaged content at check time. `git diff --cached --check` exited 0 with only a global-ignore permission warning, and `backend.test_release_candidate_gate` passed 2/2. `miniprogram/services/maps.js` remains unstaged because its filtered working-tree hash matches the index; root `project.private.config.json` is the only untracked item and is excluded. `docs/PRODUCT_WORK_LEDGER.md` was already counted in the tracked business scope, while this handoff document and `backend/test_multirole_cross_module_access.py` account for the two additions from the prior boundary.

The precise staging and cached/candidate/index boundary checks are complete. The single next action is to wait for explicit user authorization to create one immutable commit and one new tag. No commit, tag, package, push, deployment, service operation, or database operation is claimed by this section.

---

# r9 targeted release gate (2026-08-17)

Target candidate tag: `release-20260817-cross-module-freeze-r9`

r9 contains the reviewed PF01-PF06 product-feedback batch on top of immutable r8. The release manifest accepts only r9; r1 through r8 are historical and must remain rejected. This section records the product-authorized targeted gate. Commit, tag, package, and deployment identities are not asserted in advance here and must be verified directly from the generated objects.

## r9 scope and evidence

| Check | Result | Exit |
| --- | --- | ---: |
| PF01-PF06 backend and direct neighbors | 102/102 passed | 0 |
| Miniprogram inspection selection and vehicle return state | 4/4 passed | 0 |
| React existing API/logic suite | 43/43 passed | 0 |
| React PF01-PF06 pure-logic tests | 12/12 passed | 0 |
| React lint and production build | PASS; 1746 modules transformed | 0 |
| Backup and staged candidate-archive tests | 4/4 passed; archived shell shebang is LF | 0 |
| Candidate guard | 2/2 passed; r1-r8 rejected and r9 accepted | 0 |
| Targeted diff check | No whitespace errors; existing LF-to-CRLF notices only | 0 |

Product read-only Review passed after two adjacent PF06 blockers were corrected: replaced vehicles retain a returnable lifecycle, and the miniprogram “我的” page now respects the server-authoritative `can_return` value. The incorrect PF business copies in `E:\杂七杂八\水质运维` were removed by explicit whitelist; release-side scripts, inspection directories, historical packages, and product records were retained.

Real Web and miniprogram UI for r9: **NOT RUN**. Product explicitly chose to skip this regression and collect feedback from online users. Automated tests do not replace that evidence. The main residual risks are responsive layout for the new settings/cleanup interfaces, real authenticated template download, actual single-site creation, no-device empty inspection selection, and plan-level vehicle interactions on a real device.

## r9 freeze boundary

The candidate must include only reviewed source, tests, `AGENTS.md`, product/release records, and r9 gate metadata. Exclude all private configuration, TEST_UI databases and evidence, `.codex-wechat-automation`, `minitest`, temporary servers, generated front-end output, and historical source archives. The staged whitelist contains 35 files. Create one immutable commit and one annotated r9 tag, then derive the source package only from that tag. Before deployment, create and verify fresh database and uploads backups; retain r8/r6 rollback materials.

---

# r8 final release gate (2026-08-15)

Target candidate tag: `release-20260815-cross-module-freeze-r8`

r8 is the only current release candidate. r7 is immutable historical evidence and must not be moved, deleted, reused, or described as deployed. This document records the r7 pre-switch failure, the minimal r8 packaging correction, and the completed r8 automated gate. It does not claim that an immutable r8 commit, tag, package, or deployment already exists.

## r8 final automated gate

After whitelist staging, the complete r8 candidate passed:

| Check | Result | Exit |
| --- | --- | ---: |
| Backend unittest discover | 310/310 passed | 0 |
| Miniprogram Node tests | 32/32 passed | 0 |
| React `test:api` | 41/41 passed | 0 |
| React cockpit tests | 2/2 passed | 0 |
| React lint | PASS | 0 |
| React production build | PASS; 1741 modules transformed | 0 |
| Python/JavaScript syntax aggregate | 136 files passed | 0 |
| Backup and candidate-archive tests | 4/4 passed, including raw LF tar-entry verification | 0 |
| Candidate guard | 2/2 passed; r1-r7 rejected and r8 accepted | 0 |
| `git diff --cached --check` | No whitespace errors | 0 |

The backend logs for `TEST_MEDIA_FIX_notification_failure` and the work-order attachment flagging HTTP 500 are intentional failure-injection paths. Their rollback assertions passed and the complete backend suite finished successfully.

## r7 pre-switch failure

Production execution stopped safely before r6 was stopped. The r7 tag and index blob for `deploy/backup-water-monitor.sh` are LF, but Windows `git archive <tag>:平台开发` produced a CRLF shell entry because the archived subtree did not contain an explicit shell EOL attribute. The uploaded server tar entry and extracted file retained those CRLF bytes; SCP did not transform them. The installed script therefore failed at its shebang with `/usr/bin/env: 'bash\r': No such file or directory`.

r6 was never stopped and remained running and healthy. r7 was not deployed.

Operations converted only the installed server backup tool to LF and successfully ran the backup service. The resulting artifacts are:

- `water.db-20260815-113306`
- `uploads-20260815-113306.tar.gz`

Both SHA256 checks passed. This was an operational recovery and does not change the r7 tag or repository history.

## Minimal r8 correction

- `平台开发/.gitattributes` defines `*.sh text eol=lf` inside the exact subtree used as the archive root.
- `deploy/backup-water-monitor.sh` has no logic change.
- The backup regression builds a tree from the current Git index with `git write-tree`, archives `<tree>:平台开发` to memory, and verifies that the archived backup script starts with the exact LF shebang and contains no carriage-return bytes. This regression passed after whitelist staging.
- The release manifest accepts only r8; r1 through r7 are historical and rejected.

The archive regression depends on the candidate files being present in the index. Development did not stage files merely to run it; product staged the six-file whitelist and ran the regression before the r8 commit.

## Product UI status

Current-candidate business UI: **NOT RUN**.

Product explicitly authorized skipping the remaining real UI regression so actual users can test after deployment. Historical UI results are not reused as r8 evidence. Residual risk remains in miniprogram return-to-site rework, login and notification badges, and the seven Web correction groups.

## Candidate identity and freeze boundary

`deploy/release-candidates.json` accepts only `release-20260815-cross-module-freeze-r8`. All `historical_tags`, covering r1 through r7, must be rejected.

The archive regression and complete r8 automated gate have passed. The immutable commit, annotated r8 tag, and tag-derived source package may now be generated together. Their commit IDs, tag object and peeled commit, package path, size, and SHA256 must be recorded from the generated artifacts; no unknown values are asserted in advance.

## Production state and rollback

- `https://ops.hhyc-tec.cn/api/health` returned HTTP 200 during the read-only precheck.
- The current r6 container remains running and healthy.
- The TLS certificate is valid through 2026-10-28.
- The filesystem containing `/opt` had 9.6 GB available.
- The current production release record remains r6.
- The 11:33 database and uploads backups listed above have verified SHA256 values.

r8 must still be packaged from the new annotated tag and deployed. Keep the r6 release directory, image, and verified pre-r8 backups available throughout deployment. If r8 health checks, database integrity checks, or the public smoke test fails, immediately return to r6 and restore data only when required.

No commit, tag, package, push, deployment, online database write, production container change, or server operation was performed while collecting this gate evidence.
