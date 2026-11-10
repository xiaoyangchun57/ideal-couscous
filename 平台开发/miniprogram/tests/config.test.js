const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const configPath = path.resolve(__dirname, '..', 'utils', 'config.js');
const preparedValuePattern = /const PREPARED_REAL_DEVICE_API_BASE_URL = '[^']*'; \/\/ local-api-prepared-value/;

test('repository root is the only WeChat project entry', () => {
  const root = path.resolve(__dirname, '..', '..');
  const rootProject = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'));
  assert.equal(rootProject.miniprogramRoot, 'miniprogram/');
  assert.equal(rootProject.libVersion, '3.17.0');
  assert.equal(fs.existsSync(path.join(root, 'miniprogram', 'project.config.json')), false);
  assert.equal(fs.existsSync(path.join(root, 'miniprogram', 'project.config.legacy.json')), false);
});

test('miniprogram enables required component lazy loading', () => {
  const appConfig = JSON.parse(fs.readFileSync(path.resolve(__dirname, '..', 'app.json'), 'utf8'));
  assert.equal(appConfig.lazyCodeLoading, 'requiredComponents');
});

test('review baseline carries no generated real-device LAN address', () => {
  assert.match(
    fs.readFileSync(configPath, 'utf8'),
    /const PREPARED_REAL_DEVICE_API_BASE_URL = ''; \/\/ local-api-prepared-value/,
  );
});

function loadConfig(platform, envVersion, preparedUrl = '', storedOverride = '') {
  let storageReads = 0;
  const wx = {
    getSystemInfoSync: () => ({ platform }),
    getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
    getStorageSync: () => {
      storageReads += 1;
      return storedOverride;
    },
  };
  const source = fs.readFileSync(configPath, 'utf8').replace(
    preparedValuePattern,
    `const PREPARED_REAL_DEVICE_API_BASE_URL = '${preparedUrl}'; // local-api-prepared-value`,
  );
  const module = { exports: {} };
  vm.runInNewContext(source, { module, exports: module.exports, wx }, { filename: configPath });
  return { config: module.exports, storageReads };
}

test('develop simulator packages keep using localhost', () => {
  for (const platform of ['devtools', 'windows', 'mac']) {
    const { config } = loadConfig(platform, 'develop');
    assert.equal(config.BASE_URL, 'http://127.0.0.1:5000');
    assert.equal(config.API_PROFILE, 'local');
  }
});

test('develop real-device packages use the prepared private-LAN address without storage', () => {
  for (const preparedUrl of [
    'http://192.168.2.107:5000',
    'http://10.0.0.8:5000',
    'http://172.20.1.5:8080',
  ]) {
    for (const platform of ['ios', 'android']) {
      const { config, storageReads } = loadConfig(
        platform, 'develop', preparedUrl, 'http://192.168.2.88:5000',
      );
      assert.equal(config.BASE_URL, preparedUrl);
      assert.equal(config.API_PROFILE, 'local');
      assert.equal(storageReads, 0);
    }
  }
});

test('developer tools use localhost even when a real-device address is prepared', () => {
  const { config } = loadConfig('devtools', 'develop', 'http://192.168.2.88:5000');
  assert.equal(config.BASE_URL, 'http://127.0.0.1:5000');
  assert.equal(config.API_PROFILE, 'local');
});

test('develop real-device packages fail closed when no valid address was prepared', () => {
  for (const preparedUrl of ['', 'http://127.0.0.1:5000', 'http://8.8.8.8:5000', 'not-a-url']) {
    assert.throws(
      () => loadConfig('ios', 'develop', preparedUrl),
      /REAL_DEVICE_LOCAL_API_NOT_PREPARED/,
    );
  }
});

test('trial and release packages always use the online API', () => {
  for (const envVersion of ['trial', 'release']) {
    for (const platform of ['devtools', 'ios', 'android', 'windows', 'mac']) {
      const { config, storageReads } = loadConfig(
        platform, envVersion, 'http://192.168.2.107:5000', 'http://10.0.0.8:5000',
      );
      assert.equal(config.BASE_URL, 'https://ops.hhyc-tec.cn');
      assert.equal(config.API_PROFILE, 'online');
      assert.equal(storageReads, 0);
    }
  }
});

test('unknown account environment fails closed to the online API', () => {
  const { config } = loadConfig('devtools', 'unknown');
  assert.equal(config.BASE_URL, 'https://ops.hhyc-tec.cn');
  assert.equal(config.API_PROFILE, 'online');
});

// 原测试依赖 dev_scripts/prepare-miniprogram-local-api.ps1（本机私有、仓库从未提交）
// 且 hardcode powershell.exe，Linux CI 不可能通过。改为跨平台验证同一份契约：
// config.js 只接受私网 IPv4 + 端口，公网地址与 localhost 一律失败关闭。
test('prepared LAN address is accepted and non-private addresses fail closed', () => {
  const { config } = loadConfig('android', 'develop', 'http://192.168.50.23:5000');
  assert.equal(config.API_PROFILE, 'local');
  assert.equal(config.BASE_URL, 'http://192.168.50.23:5000');
  assert.throws(() => loadConfig('android', 'develop', 'http://203.0.113.10:5000'),
    /REAL_DEVICE_LOCAL_API_NOT_PREPARED/);
  assert.throws(() => loadConfig('android', 'develop', 'http://127.0.0.1:5000'),
    /REAL_DEVICE_LOCAL_API_NOT_PREPARED/);
});
