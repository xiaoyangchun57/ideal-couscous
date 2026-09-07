const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');

assert.doesNotMatch(appSource, /onLaunch\(\)[\s\S]{0,700}bindOpenId\(/);
assert.match(appSource, /微信绑定只由用户在订阅动作后显式确认/);

console.log('appBindingSafety tests passed');
