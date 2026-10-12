const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// 站点详情页设计系统整改：最小结构断言
(async () => {
  try {
    const wxml = fs.readFileSync(path.join(__dirname, '../pages/site/site.wxml'), 'utf8');
    const wxssBuf = fs.readFileSync(path.join(__dirname, '../pages/site/site.wxss'));
    const wxss = wxssBuf.toString('utf8');

    // 1. 类型标签去品牌色，改中性类型标签
    assert.doesNotMatch(wxml, /tag-brand/, 'site.wxml 不应再有 tag-brand');
    assert.match(wxml, /class="tag tag-neutral"/, '站点类型标签应为 tag-neutral');

    // 2. Sheet 高度铁律 75vh
    assert.doesNotMatch(wxss, /max-height:\s*80vh/, 'sheet 不应再有 80vh');
    assert.match(wxss, /\.sheet\s*\{[^}]*max-height:\s*75vh/, 'sheet max-height 应为 75vh');

    // 3. 无硬编码 font-size / font-weight 数字
    assert.doesNotMatch(wxss, /font-size:\s*\d+(\.\d+)?rpx/, '不应有硬编码 font-size');
    assert.doesNotMatch(wxss, /font-weight:\s*\d+/, '不应有硬编码 font-weight');

    // 4. 无硬编码 padding/margin/gap/border-radius rpx（token 化）
    assert.doesNotMatch(wxss, /(padding|margin|gap|border-radius)(-[a-z]+)?:[^;}]*\d+(\.\d+)?rpx/, '不应有硬编码间距/圆角 rpx');

    // 5. 状态 chip 胶囊形
    assert.match(wxss, /\.mon-status-chip\s*\{[^}]*border-radius:\s*var\(--radius-full\)/, 'mon-status-chip 应为 --radius-full');

    // 6. Section 标题节奏
    assert.match(wxss, /\.site-info-heading\s*\{[^}]*margin-top:\s*var\(--space-8\)/, 'site-info-heading margin-top 应为 --space-8');

    // 7. 括号配对 + 无 BOM
    assert.equal((wxss.match(/\{/g) || []).length, (wxss.match(/\}/g) || []).length, '花括号应配对');
    assert.ok(!(wxssBuf[0] === 0xEF && wxssBuf[1] === 0xBB && wxssBuf[2] === 0xBF), '不应有 UTF-8 BOM');

    console.log('siteDesignTokens tests passed');
  } catch (err) {
    console.error('siteDesignTokens tests FAILED:', err.message);
    process.exitCode = 1;
  }
})();
