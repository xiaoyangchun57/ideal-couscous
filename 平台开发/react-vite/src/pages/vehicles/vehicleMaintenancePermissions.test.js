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

test('admin maintenance and correction controls keep server-managed boundaries', () => {
  assert.match(source, /name="maint_at" label="实际保养时间"/);
  assert.match(source, /name="mileage_at" label="实际保养里程"/);
  assert.match(source, /name="items" label="保养项目 \/ 材料"/);
  assert.match(source, /name="next_maint_mileage" label="下次保养里程"/);
  assert.match(source, /_idempotency_key: maintenanceIdempotencyKey\.current/);
  assert.match(source, /const editableValues = isAdmin \? values/);
  assert.match(source, /name="correction_reason" label="纠错原因"/);
  assert.match(source, /!\['in_use', 'restricted'\]\.includes\(value\)/);
  assert.match(source, /name="current_mileage" label="当前里程（纠错）"[\s\S]*disabled=\{activeVehicle\?\.status === 'in_use'\}/);
  assert.match(source, /name="next_maintenance_mileage" label="保养基线 \/ 下次保养里程（纠错）"[\s\S]*disabled=\{activeVehicle\?\.status === 'in_use'\}/);
  assert.doesNotMatch(source, /下次保养里程（当前里程 \+ 5000 km）/);
});

test('maintenance guidance distinguishes admin history correction from operator locked baselines', () => {
  assert.match(source, /仅当前最新有效保养会更新台账保养时间和提醒，较早历史补录只保留历史/);
  assert.match(source, /车辆当前里程不会因保养记录回写/);
  assert.match(source, /普通运维按车辆台账当前里程登记，不可补改历史里程/);
  assert.match(source, /普通运维保存时由系统按当前台账里程计算提醒基线/);
  assert.match(source, /name="mileage_at"[\s\S]*disabled=\{!isAdmin\}/);
  assert.match(source, /name="next_maint_mileage"[\s\S]*disabled=\{!isAdmin\}/);
});

test('vehicle edit and correction history are wired with independent pending and stale-request guards', () => {
  assert.match(source, /const \[submittingEditVehicle, setSubmittingEditVehicle\] = useState\(false\)/);
  assert.match(source, /runVehicleEditSubmission\(\{/);
  assert.match(source, /guard: editVehicleSubmittingRef/);
  assert.match(source, /confirmLoading=\{submittingEditVehicle\}/);
  assert.match(source, /keyboard=\{!submittingEditVehicle\}/);
  assert.match(source, /maskClosable=\{!submittingEditVehicle\}/);
  assert.match(source, /operation-logs\?module=vehicle&target_type=vehicle&target_id=\$\{vehicleId\}&limit=50/);
  assert.match(source, /vehicleCorrectionRequestRef\.current !== requestId/);
  assert.match(source, /\.filter\(row => row\.action === 'admin_correction'\)/);
  assert.match(source, /message="纠错记录加载失败"/);
  assert.match(source, /loadVehicleCorrectionLogs\(activeVehicle\.id\)\}>重试/);
  assert.match(source, /emptyText: '暂无管理员纠错记录'/);
});

test('in-use guidance exposes only restriction without implying occupancy release', () => {
  assert.match(source, /可以登记为“限制使用”，但原行程和申请仍须由还车流程结束/);
  assert.match(source, /在途时仅可追加“限制使用”；真实占用仍由还车流程释放/);
  assert.doesNotMatch(source, /状态不可手动更改；请在还车流程完成后由系统自动释放/);
});

test('admin can complete one real maintenance record without fabricating a replacement record', () => {
  assert.match(source, /const \[completingMaintenanceId, setCompletingMaintenanceId\] = useState\(null\)/);
  assert.match(source, /api\.putStrict\(`\/vehicle\/maintenance\/\$\{record\.id\}\/complete`/);
  assert.match(source, /\['open', 'in_progress'\]\.includes\(record\.maint_status\)/);
  assert.match(source, /dataIndex: 'actual_return_at'/);
  assert.match(source, /icon=\{<CheckOutlined \/>\}/);
});
