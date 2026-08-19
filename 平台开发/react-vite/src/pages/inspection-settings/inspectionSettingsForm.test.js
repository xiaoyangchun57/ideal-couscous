import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {
  inspectionItemAccessState, inspectionItemFormValues,
  inspectionItemRefreshNotice, inspectionTemplateFormValues, inspectionTemplatePayload,
} from './inspectionSettingsForm.js';

test('normalizes persisted photo flag and sort order for the item form', () => {
  assert.deepEqual(inspectionItemFormValues({
    item_name: '浊度',
    category: '水质',
    frequency_level: 'high',
    photo_required: 1,
    need_review: 1,
    max_photos: '2',
    inspection_standard: '读数在合格范围内',
    sort_order: '3',
  }, 5), {
    item_name: '浊度',
    category: '水质',
    photo_required: true,
    need_review: true,
    max_photos: 2,
    inspection_standard: '读数在合格范围内',
    sort_order: 3,
  });
});

test('provides stable defaults for a newly added item', () => {
  assert.deepEqual(inspectionItemFormValues(null, 2), {
    item_name: '',
    category: '',
    photo_required: false,
    need_review: false,
    max_photos: 0,
    inspection_standard: '',
    sort_order: 3,
  });
});

test('template category is internal: edits preserve history and new templates use water quality', () => {
  assert.deepEqual(inspectionTemplateFormValues({
    template_name: '旧模板', category: '历史分类', frequency: 'monthly', description: '说明',
    status: 'inactive', sort_order: '4',
  }), {
    template_name: '旧模板', frequency: 'monthly', description: '说明',
  });
  assert.deepEqual(inspectionTemplatePayload({
    template_name: '旧模板', frequency: 'monthly', description: '更新',
    status: 'inactive', sort_order: 4,
  }, { category: '历史分类' }), {
    template_name: '旧模板', frequency: 'monthly', description: '更新', category: '历史分类',
  });
  assert.equal(inspectionTemplatePayload({ template_name: '新模板', frequency: 'weekly' }, null).category, '水质');
  assert.deepEqual(inspectionTemplateFormValues(null), {
    template_name: '', frequency: undefined, description: '',
  });
});

test('item load failure is not presented as an editable empty list', () => {
  assert.deepEqual(inspectionItemAccessState({ loading: false, error: '读取失败', saving: false }), {
    canMutate: false, showEmpty: false,
  });
  assert.deepEqual(inspectionItemAccessState({ loading: false, error: '', saving: false }), {
    canMutate: true, showEmpty: true,
  });
});

test('a successful write remains successful when the following refresh fails', () => {
  assert.equal(inspectionItemRefreshNotice(true), '');
  assert.match(inspectionItemRefreshNotice(false), /已保存.*刷新失败/);
});

test('item dialog prevents duplicate actions while saving', () => {
  const source = fs.readFileSync(path.join(
    import.meta.dirname, 'InspectionSettingsPage.jsx',
  ), 'utf8');
  assert.match(source, /open=\{itemOpen\}[\s\S]*?confirmLoading=\{itemSaving\}/);
  assert.match(source, /onCancel=\{\(\) => !itemSaving && setItemOpen\(false\)\}/);
});

test('template frequency is explicit, Chinese and leaves legacy values for manual handling', () => {
  const source = fs.readFileSync(path.join(
    import.meta.dirname, 'InspectionSettingsPage.jsx',
  ), 'utf8');
  for (const pair of [
    ["weekly", '周检'], ["monthly", '月检'],
    ["quarterly", '季检'], ["yearly", '年检'],
  ]) {
    assert.match(source, new RegExp(`value: '${pair[0]}', label: '${pair[1]}'`));
  }
  assert.match(source, /name="frequency"[\s\S]*rules=\{\[\{ required: true/);
  assert.match(source, /row\.frequency === 'annual' \? 'yearly'/);
  assert.match(source, /FREQUENCIES\.some\(option => option\.value === row\.frequency\) \? row\.frequency : undefined/);
  assert.match(source, /需人工处理/);
  assert.doesNotMatch(source, /站点类型匹配|frequency_level/);
  assert.doesNotMatch(source, /name="status" label="状态"/);
  assert.doesNotMatch(source, /title: '状态'|title: '排序'/);
});

test('retired device applicability UI and requests are removed rather than hidden', () => {
  const source = fs.readFileSync(path.join(
    import.meta.dirname, 'InspectionSettingsPage.jsx',
  ), 'utf8');
  assert.match(source, /api\.getStrict\('\/inspection-v2\/templates'\)/);
  assert.doesNotMatch(source, /\/inspection-v2\/configs/);
  assert.doesNotMatch(source, /设备适用规则|适用设备|deviceTypeMap|DEVICE_TYPE_OPTIONS/);
  assert.doesNotMatch(source, /configOpen|configForm|configSaving|openConfig|saveConfig|removeConfig/);
  assert.doesNotMatch(source, /inspectionConfigFormValues/);
});

test('template category is not user-maintainable while inspection item category remains', () => {
  const source = fs.readFileSync(path.join(
    import.meta.dirname, 'InspectionSettingsPage.jsx',
  ), 'utf8');
  assert.doesNotMatch(source, /label="模板分类"/);
  assert.equal((source.match(/title: '分类', dataIndex: 'category'/g) || []).length, 1);
  assert.match(source, /inspectionTemplatePayload\(values, template\)/);
  assert.match(source, /name="category" label="分类"/);
});
