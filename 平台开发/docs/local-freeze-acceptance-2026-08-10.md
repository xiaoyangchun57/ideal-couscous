# Image evidence correction acceptance

Product real UI review completed on 2026-08-11. Final full automation completed on 2026-08-12. The detailed release evidence is maintained in `RELEASE_TEST_HANDOFF.md`.

| Problem | User impact | Target result | Current status | Acceptance result |
| --- | --- | --- | --- | --- |
| Delete/void dialog overlap | Metadata and actions were obscured at narrower widths | Stable layout and readable long filenames at 1440x900 and 1024x768 | Complete | PASS in real Web UI |
| Photos not tied to inspection items | Same-site photos could not be distinguished or traced to the authoritative item | Exact structured ownership with `#102 -> #7012`, `#101 -> #7013`, `#96 -> #7014` everywhere | Complete | PASS in list, detail, review, and confirmation UI |
| Approved state conflicted with risk flag | Users saw "已通过" and "需复核" as simultaneous current states | One current review state with risk retained only as history | Complete | PASS in real UI |
| Incorrect approved evidence lacked a safe correction loop | Formal evidence could be deleted or lose audit continuity | Void without deleting the file/history, request supplement, re-review replacement, restore item to effective | Complete | PASS in Web and WeChat real UI |

Historical linkage uses normalized full-path equality between `operation_attachments.stored_path` and structured URLs in `insp_plan_items.photo_urls`. Protocol/host, query string, and consistent URL encoding may be normalized; basename-only, fuzzy, site, or time inference is forbidden. Only one unique match is backfilled. Zero/multiple matches remain pending confirmation, existing valid ownership is protected, conflicts enter an exception list, and repeat migration is idempotent.

Residual P2: the statistics label "影像总数" counts 13 effective evidence records while the default list shows 18 records including five rejected audit records. The values are correct but the label can be made more explicit later. This does not block r5.

No push, deployment, online database/container operation, or historical-tag movement is authorized by this acceptance record.
