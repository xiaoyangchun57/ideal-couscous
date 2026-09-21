import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('./VehiclesPage.jsx', import.meta.url), 'utf8');

test('vehicle inspection and refueling actions use the shared maintenance permission', () => {
  assert.match(source, /const canMaintain = isAdmin \|\| roles\.includes\('operator'\)/);
  assert.match(source, /\{canMaintain && <Tooltip title="登记车况检查"/);
  assert.match(source, /\{canMaintain && <Button icon=\{<SafetyCertificateOutlined \/>\}/);
  assert.match(source, /\{canMaintain && <Button icon=\{<FireOutlined \/>\}/);
  assert.doesNotMatch(source, /^\s*<Tooltip title="登记车况检查"/m);
  assert.doesNotMatch(source, /^\s*<Button icon=\{<FireOutlined \/>\}/m);
});
