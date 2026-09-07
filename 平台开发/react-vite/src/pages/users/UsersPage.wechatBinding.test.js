import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, 'UsersPage.jsx'), 'utf8');

assert.match(source, /dataIndex: 'wechat_bound'/);
assert.match(source, /record\.wechat_bound \? \[/);
assert.match(source, /解除微信绑定/);
assert.match(source, /请填写解除微信绑定原因/);
assert.match(source, /deleteStrict\(`\/users\/\$\{record\.id\}\/wechat-binding`/);
assert.match(source, /current_session_revoked === true/);
assert.match(source, /finishWechatBindingUnbind/);
assert.match(source, /window\.location\.replace\('\/login'\)/);

console.log('UsersPage wechat binding tests passed');
