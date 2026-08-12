# r5 final release gate (2026-08-12)

Candidate: `release-20260811-cross-module-freeze-r5`

This is the sole deployment candidate produced from the product-approved local development workspace. It does not authorize push, deployment, or any online database/container operation. Historical r1-r4 tags remain unchanged.

## Product real UI acceptance

Product review passed all four image-evidence corrections:

- Dialog layout passed at 1440x900 and 1024x768. Long filenames wrap without overlapping the content or footer actions.
- Attachment attribution is consistent in list, detail, review, and void confirmation: `#102 -> item #7012`, `#101 -> item #7013`, and `#96 -> item #7014`.
- Approved risk evidence has one current state, `approved`, with the historical note "曾触发风险/人工已核对" instead of a conflicting current "需复核" state.
- The void/supplement/re-review closure passed: original attachment `#990401` is `voided` with `is_deleted=0`; replacement `#990406` is `approved`; item `#990301` is `effective`; the reason, target, original file, and audit history remain traceable.

WeChat real UI also passed notification targeting, target-item highlighting, supplement upload, re-review, restoration to effective, and rapid-click single-dialog/cancel/failure-lock-release behavior through the existing IDE automation endpoint.

## Final full test gate

All commands ran against the local development workspace. Runtime-only UI fixtures (`test_api.py`, `test_ui_media_fixture.py`, and `test_ui_media_add_ordinary.py`) were excluded from the backend unittest discovery because they require external services or create isolated UI data.

| Check | Result | Exit |
| --- | --- | ---: |
| Backend full unittest | 249 tests, OK (2026-08-12 10:22:00-10:22:46 +08:00) | 0 |
| Miniprogram Node tests | all 11 test files passed | 0 |
| Miniprogram/backend syntax aggregator | 122 files passed | 0 |
| React `test:api` | 39/39 passed | 0 |
| React lint | ESLint passed (10:25:45-10:25:47 +08:00) | 0 |
| React production build | Vite build passed, 1742 modules transformed (10:25:52-10:25:56 +08:00) | 0 |
| Backend Python compile | 75 Python files passed (10:24:59-10:25:09 +08:00) | 0 |
| Candidate guard tests | 2/2 passed; r1-r4 rejected and r5 accepted | 0 |
| `git diff --check` | no whitespace errors; line-ending warnings only | 0 |

The `TEST_MEDIA_FIX_notification_failure` stack trace emitted during backend tests is the intentional failure-injection path used to verify transaction rollback; the suite completed successfully.

## Candidate guard

`deploy/release-candidates.json` accepts only `release-20260811-cross-module-freeze-r5`. These historical tags are explicitly rejected and must not be moved or deployed:

- `release-20260810-cross-module-freeze`
- `release-20260810-cross-module-freeze-r2`
- `release-20260810-cross-module-freeze-r3`
- `release-20260811-cross-module-freeze-r4`

## Package boundary

The source package is generated with `git archive` from the annotated r5 tag, with explicit path exclusions for the two historically tracked WeChat IDE private configuration files. It contains only committed source and excludes isolated databases, test uploads/evidence, automation caches/scripts, temporary UI fixtures/config, `.git`, root-level untracked IDE configuration, `平台开发/project.private.config.json`, and `平台开发/miniprogram/project.private.config.json`.

## Residual boundary

`stats.total=13` and the default list count `18` intentionally represent different sets: statistics count effective evidence, while the default list also retains five rejected audit records and excludes voided records. This is not a calculation defect. The label "影像总数" can later be renamed to "有效影像" or paired with a list-count value; this is a non-blocking P2 wording improvement.

Deployment must still independently confirm the production domain, certificate, backup, migration, and rollback configuration. No push, deployment, or online operation was performed by this release gate.
