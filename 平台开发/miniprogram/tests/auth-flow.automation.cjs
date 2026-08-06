const assert = require('node:assert/strict');

const automator = require(process.env.MINIPROGRAM_AUTOMATOR_PATH);

const requiredEnv = [
  'WECHAT_CLI_PATH',
  'MINIPROGRAM_PROJECT_PATH',
  'TEST_USERNAME',
  'TEST_TEMPORARY_PASSWORD',
  'TEST_NEW_PASSWORD',
];

for (const name of requiredEnv) {
  if (!process.env[name]) throw new Error(`Missing environment variable: ${name}`);
}

async function poll(getValue, predicate, description, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await getValue();
    if (predicate(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${description}`);
}

async function inputAll(page, values) {
  const inputs = await page.$$('.input');
  assert.equal(inputs.length, values.length, 'Unexpected number of login inputs');
  for (let index = 0; index < values.length; index += 1) {
    await inputs[index].input(values[index]);
  }
}

async function main() {
  let miniProgram;
  try {
    miniProgram = process.env.WECHAT_AUTOMATION_WS_ENDPOINT
      ? await automator.connect({ wsEndpoint: process.env.WECHAT_AUTOMATION_WS_ENDPOINT })
      : await automator.launch({
        cliPath: process.env.WECHAT_CLI_PATH,
        projectPath: process.env.MINIPROGRAM_PROJECT_PATH,
        trustProject: true,
        timeout: 60000,
        args: process.env.WECHAT_CLI_JS ? [process.env.WECHAT_CLI_JS] : [],
      });

    await miniProgram.mockWxMethod('login', { errMsg: 'login:ok', code: '' });
    let page = await miniProgram.reLaunch('/pages/login/login');
    await page.waitFor('.login-btn');

    assert.equal(page.path, 'pages/login/login');
    assert.equal((await page.data()).mustChangePassword, false);

    await inputAll(page, [process.env.TEST_USERNAME, process.env.TEST_TEMPORARY_PASSWORD]);
    await (await page.$('.login-btn')).tap();
    await poll(
      () => page.data(),
      (data) => data.mustChangePassword === true,
      'forced password-change form',
    );

    await inputAll(page, ['123', '123']);
    await (await page.$('.login-btn')).tap();
    const invalidData = await poll(
      () => page.data(),
      (data) => data.error === '新密码至少8位',
      'minimum password-length validation',
    );
    assert.equal(invalidData.mustChangePassword, true);

    await inputAll(page, [process.env.TEST_NEW_PASSWORD, process.env.TEST_NEW_PASSWORD]);
    await (await page.$('.login-btn')).tap();
    page = await poll(
      () => miniProgram.currentPage(),
      (current) => current && current.path === 'pages/index/index',
      'index page after password change',
    );

    const minePage = await miniProgram.switchTab('/pages/mine/mine');
    await minePage.waitFor('.logout');
    const mineData = await poll(
      () => minePage.data(),
      (data) => data.realName === 'B0未分站运维',
      'operator profile data',
    );
    assert.equal(mineData.sitesCount, 0);

    await miniProgram.mockWxMethod('showModal', {
      errMsg: 'showModal:ok',
      confirm: true,
      cancel: false,
    });
    await (await minePage.$('.logout')).tap();
    page = await poll(
      () => miniProgram.currentPage(),
      (current) => current && current.path === 'pages/login/login',
      'login page after logout',
    );
    assert.equal(page.path, 'pages/login/login');

    console.log(JSON.stringify({
      forcedPasswordChange: true,
      minimumLengthValidation: true,
      passwordChangeCompleted: true,
      unassignedSiteCount: mineData.sitesCount,
      serverLogoutReturnedToLogin: true,
    }));
  } finally {
    if (miniProgram) await miniProgram.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
