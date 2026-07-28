"""One-time migration for the three-role user model.

Run after backing up data/water.db. It deliberately refuses to remove a viewer
if any business table still points at that account.
"""
import os
import shutil
import sqlite3
from datetime import datetime


BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, 'data', 'water.db')


def main():
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = f'{DB_PATH}.before_user_role_migration_{stamp}.bak'
    shutil.copy2(DB_PATH, backup)
    print(f'Backup: {backup}')

    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        columns = {row['name'] for row in db.execute('PRAGMA table_info(users)')}
        if 'login_name' not in columns:
            db.execute("ALTER TABLE users ADD COLUMN login_name TEXT DEFAULT ''")
        db.execute('''CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            PRIMARY KEY (user_id, role),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')
        db.execute('''INSERT OR IGNORE INTO user_roles (user_id, role)
            SELECT id, CASE role WHEN 'manager' THEN 'admin' WHEN 'inspector' THEN 'reviewer' ELSE role END
            FROM users WHERE role IN ('admin', 'manager', 'operator', 'reviewer', 'inspector')''')
        db.execute("UPDATE users SET role='admin' WHERE role='manager'")
        db.execute("UPDATE users SET role='reviewer' WHERE role='inspector'")

        for row in db.execute('SELECT id, real_name, role, login_name FROM users').fetchall():
            if row['login_name']:
                continue
            duplicate = db.execute('SELECT COUNT(*) FROM users WHERE real_name=?', (row['real_name'],)).fetchone()[0] > 1
            suffix = {'admin': '管理员', 'operator': '运维', 'reviewer': '审核'}.get(row['role'], '账号')
            login_name = f"{row['real_name']}-{suffix}" if duplicate else row['real_name']
            db.execute('UPDATE users SET login_name=? WHERE id=?', (login_name, row['id']))

        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login_name ON users(login_name) WHERE login_name != ''")

        viewer_rows = db.execute("SELECT id, username, real_name FROM users WHERE role='viewer'").fetchall()
        for viewer in viewer_rows:
            references = []
            for table in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                table_name = table['name']
                if table_name in ('users', 'user_sites', 'user_roles', 'notifications'):
                    continue
                fields = [f['name'] for f in db.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
                for field in fields:
                    if field in ('user_id', 'reviewer_id', 'assignee_id', 'requester_id', 'approver_id', 'operator_id'):
                        count = db.execute(f'SELECT COUNT(*) FROM "{table_name}" WHERE "{field}"=?', (viewer['id'],)).fetchone()[0]
                        if count:
                            references.append(f'{table_name}.{field}={count}')
            if references:
                raise RuntimeError(f"Refusing to delete {viewer['real_name']}: {', '.join(references)}")
            db.execute('DELETE FROM notifications WHERE user_id=?', (viewer['id'],))
            db.execute('DELETE FROM user_sites WHERE user_id=?', (viewer['id'],))
            db.execute('DELETE FROM user_roles WHERE user_id=?', (viewer['id'],))
            db.execute('DELETE FROM users WHERE id=?', (viewer['id'],))
            print(f"Deleted viewer account: {viewer['real_name']} ({viewer['username']})")

        db.commit()


if __name__ == '__main__':
    main()
