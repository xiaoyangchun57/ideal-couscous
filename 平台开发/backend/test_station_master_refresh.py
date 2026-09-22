import sqlite3
from contextlib import closing
from pathlib import Path
from xml.sax.saxutils import escape
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_station_ingestion import apply_migration
import station_master_refresh as refresh


def write_workbook(path: Path, rows, *, numeric_mn=frozenset()):
    sheet_rows = []
    all_rows = [("站点名称", "站码"), *rows]
    for number, (name, mn) in enumerate(all_rows, 1):
        cells = []
        if name is not None:
            cells.append(f'<c r="A{number}" t="inlineStr"><is><t>{escape(str(name))}</t></is></c>')
        if mn is not None:
            if number in numeric_mn:
                cells.append(f'<c r="B{number}"><v>{escape(str(mn))}</v></c>')
            else:
                cells.append(f'<c r="B{number}" t="inlineStr"><is><t>{escape(str(mn))}</t></is></c>')
        sheet_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet><sheetData>' + "".join(sheet_rows) + '</sheetData></worksheet>',
        )


class StationMasterRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "station-master.db"
        self.workbook = self.root / "station-master.xlsx"
        self.backups = self.root / "backups"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """CREATE TABLE sites(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT UNIQUE NOT NULL,
                       name TEXT NOT NULL,type TEXT NOT NULL)"""
            )
            connection.executemany(
                "INSERT INTO sites(id,code,name,type) VALUES (?,?,?,'water_quality')",
                ((1, "OLD001", "旧一厂"), (2, "OLD002", "蛇山"), (3, "OLD003", "坝上旧名")),
            )
            connection.execute(
                "CREATE TABLE historical_site_links(id INTEGER PRIMARY KEY,site_id INTEGER REFERENCES sites(id),note TEXT)"
            )
            connection.executemany(
                "INSERT INTO historical_site_links(site_id,note) VALUES (?,?)", ((1, "kept"), (2, "retired kept")),
            )
            connection.commit()
        apply_migration(self.database, self.root / "migration-backups")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO site_name_aliases(site_id,alias_name,normalized_alias) VALUES (3,'坝上','坝上')"
            )
            connection.executemany(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,enabled,endpoint_state)
                   VALUES (?,?,?,?,?)""",
                (("MN001", "h1", 1, 1, "bound"),
                 ("OLDMN", "h2", 2, 1, "bound"),
                 ("62305550", "h3", None, 0, "disabled")),
            )
            connection.commit()
        write_workbook(
            self.workbook,
            (("新一厂", "MN001"), ("坝上", "62305550"), ("新增站", "MN300")),
            numeric_mn={3},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_apply_verify_preserves_ids_history_and_is_repeatable(self):
        plan = refresh.preview_station_master_refresh(self.database, self.workbook)
        self.assertEqual(
            {key: plan[key] for key in (
                "accepted_rows", "exact_mn_count", "alias_match_count", "created_count",
                "retired_count", "rebound_endpoint_count", "disabled_endpoint_count",
            )},
            {"accepted_rows": 3, "exact_mn_count": 1, "alias_match_count": 1,
             "created_count": 1, "retired_count": 1, "rebound_endpoint_count": 1,
             "disabled_endpoint_count": 1},
        )
        self.assertEqual(plan["conflicts"], [])

        result = refresh.apply_station_master_refresh(
            self.database, self.workbook, backup_dir=self.backups,
            expected_fingerprint=plan["preview_fingerprint"], expected_row_count=3,
            offline_confirmed=True, retirement_confirmed=True,
        )

        self.assertEqual(result["result"], "applied")
        self.assertTrue(Path(result["backup"]).is_file())
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT id,code,name,master_status FROM sites ORDER BY id"
            ).fetchall(), [
                (1, "MN001", "新一厂", "active"),
                (2, "OLD002", "蛇山", "retired"),
                (3, "62305550", "坝上", "active"),
                (4, "MN300", "新增站", "active"),
            ])
            self.assertEqual(connection.execute(
                "SELECT site_id,note FROM historical_site_links ORDER BY id"
            ).fetchall(), [(1, "kept"), (2, "retired kept")])
            self.assertEqual(set(connection.execute(
                "SELECT site_id,alias_name FROM site_name_aliases"
            ).fetchall()), {(1, "旧一厂"), (3, "坝上"), (3, "坝上旧名")})
            self.assertEqual(connection.execute(
                "SELECT business_site_id,enabled,endpoint_state FROM trusted_endpoints WHERE station_code='62305550'"
            ).fetchone(), (3, 1, "bound"))
            self.assertEqual(connection.execute(
                "SELECT enabled,endpoint_state FROM trusted_endpoints WHERE station_code='OLDMN'"
            ).fetchone(), (0, "disabled"))
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_master_refresh_audits"
            ).fetchone()[0], 1)
        verified = refresh.verify_station_master_refresh(
            self.database, self.workbook,
            expected_source_fingerprint=plan["source_fingerprint"],
        )
        self.assertEqual(verified["result"], "verified")
        repeated_plan = refresh.preview_station_master_refresh(self.database, self.workbook)
        repeated = refresh.apply_station_master_refresh(
            self.database, self.workbook, backup_dir=self.backups,
            expected_fingerprint=repeated_plan["preview_fingerprint"], expected_row_count=3,
            offline_confirmed=True, retirement_confirmed=True,
        )
        self.assertEqual(repeated["result"], "already_applied")
        self.assertIsNone(repeated["backup"])

    def test_apply_requires_confirmations_and_current_preview(self):
        plan = refresh.preview_station_master_refresh(self.database, self.workbook)
        with self.assertRaisesRegex(refresh.StationMasterRefreshError, "confirmation"):
            refresh.apply_station_master_refresh(
                self.database, self.workbook, backup_dir=self.backups,
                expected_fingerprint=plan["preview_fingerprint"], expected_row_count=3,
                offline_confirmed=False, retirement_confirmed=True,
            )
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE sites SET name='预览后变化' WHERE id=1")
            connection.commit()
        with self.assertRaisesRegex(refresh.StationMasterRefreshError, "changed after preview"):
            refresh.apply_station_master_refresh(
                self.database, self.workbook, backup_dir=self.backups,
                expected_fingerprint=plan["preview_fingerprint"], expected_row_count=3,
                offline_confirmed=True, retirement_confirmed=True,
            )
        self.assertFalse(self.backups.exists())

    def test_apply_failure_rolls_back_all_database_writes(self):
        plan = refresh.preview_station_master_refresh(self.database, self.workbook)

        def fail_after_write(connection, _plan):
            connection.execute("UPDATE sites SET name='不应保留' WHERE id=1")
            raise RuntimeError("forced failure")

        with mock.patch.object(refresh, "_apply_plan", side_effect=fail_after_write):
            with self.assertRaisesRegex(RuntimeError, "forced failure"):
                refresh.apply_station_master_refresh(
                    self.database, self.workbook, backup_dir=self.backups,
                    expected_fingerprint=plan["preview_fingerprint"], expected_row_count=3,
                    offline_confirmed=True, retirement_confirmed=True,
                )
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM sites WHERE id=1").fetchone()[0], "旧一厂")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM station_master_refresh_audits").fetchone()[0], 0)
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1)

    def test_duplicate_or_incomplete_mn_is_rejected_before_database_access(self):
        duplicate = self.root / "duplicate.xlsx"
        write_workbook(duplicate, (("甲", "MN001"), ("乙", "MN001")))
        with self.assertRaisesRegex(refresh.StationMasterRefreshError, "duplicate MN"):
            refresh.load_station_master_workbook(duplicate)
        incomplete = self.root / "incomplete.xlsx"
        write_workbook(incomplete, (("甲", None),))
        with self.assertRaisesRegex(refresh.StationMasterRefreshError, "incomplete"):
            refresh.load_station_master_workbook(incomplete)

    def test_ambiguous_alias_is_reported_and_blocks_apply(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO sites(code,name,type) VALUES ('OLD004','另一旧站','water_quality')")
            site_id = connection.execute("SELECT id FROM sites WHERE code='OLD004'").fetchone()[0]
            connection.execute(
                "INSERT INTO site_name_aliases(site_id,alias_name,normalized_alias) VALUES (?,?,?)",
                (site_id, "坝上", "坝上"),
            )
            connection.commit()
        plan = refresh.preview_station_master_refresh(self.database, self.workbook)
        self.assertIn("name_ambiguous", {item["category"] for item in plan["conflicts"]})
        with self.assertRaisesRegex(refresh.StationMasterRefreshError, "unresolved conflicts"):
            refresh.apply_station_master_refresh(
                self.database, self.workbook, backup_dir=self.backups,
                expected_fingerprint=plan["preview_fingerprint"], expected_row_count=3,
                offline_confirmed=True, retirement_confirmed=True,
            )

    def test_current_monitoring_profile_for_another_site_blocks_endpoint_rebinding(self):
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint_id = connection.execute(
                "SELECT id FROM trusted_endpoints WHERE station_code='62305550'"
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(
                       endpoint_id,business_site_id,timezone,effective_from)
                   VALUES (?,1,'Asia/Shanghai','2026-09-22T00:00:00+00:00')""",
                (endpoint_id,),
            )
            connection.commit()
        plan = refresh.preview_station_master_refresh(self.database, self.workbook)
        self.assertIn(
            "endpoint_profile_binding_conflict",
            {item["category"] for item in plan["conflicts"]},
        )
        with self.assertRaisesRegex(refresh.StationMasterRefreshError, "unresolved conflicts"):
            refresh.apply_station_master_refresh(
                self.database, self.workbook, backup_dir=self.backups,
                expected_fingerprint=plan["preview_fingerprint"], expected_row_count=3,
                offline_confirmed=True, retirement_confirmed=True,
            )
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT business_site_id,enabled,endpoint_state FROM trusted_endpoints WHERE id=?",
                (endpoint_id,),
            ).fetchone(), (None, 0, "disabled"))


if __name__ == "__main__":
    unittest.main()
