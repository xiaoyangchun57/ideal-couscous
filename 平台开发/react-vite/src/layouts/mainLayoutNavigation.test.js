import test from 'node:test';
import assert from 'node:assert/strict';
import { mainLayoutLocation } from './mainLayoutNavigation.js';

const meta = {
  '/': { title: '驾驶舱' }, '/sites': { title: '站点全景' },
  '/sites/data-access': { title: '接入观察' },
};
const nav = ['/', '/sites'];

test('site directory, details and access center keep their exact title and parent navigation', () => {
  for (const [pathname, metaKey] of [['/sites', '/sites'], ['/sites/7', '/sites'], ['/sites/data-access', '/sites/data-access'], ['/sites/data-access/', '/sites/data-access']]) {
    const location = mainLayoutLocation(pathname, meta, nav);
    assert.equal(location.metaKey, metaKey);
    assert.equal(location.meta.title, meta[metaKey].title);
    assert.equal(location.selectedKey, '/sites');
  }
});

test('unknown paths never inherit cockpit or a misleading site breadcrumb', () => {
  for (const pathname of ['/unknown', '/sites/7/unknown', '/sites/data-access/unknown']) {
    const location = mainLayoutLocation(pathname, meta, nav);
    assert.equal(location.metaKey, null);
    assert.equal(location.selectedKey, null);
    assert.equal(location.meta.title, '页面不存在');
  }
  assert.equal(mainLayoutLocation('/', meta, nav).selectedKey, '/');
});

test('the longest available navigation path wins without adding an unauthorized menu item', () => {
  assert.equal(mainLayoutLocation('/sites/data-access', meta, [...nav, '/sites/data-access']).selectedKey, '/sites/data-access');
  assert.equal(mainLayoutLocation('/sites/7', meta, ['/']).selectedKey, null);
});
