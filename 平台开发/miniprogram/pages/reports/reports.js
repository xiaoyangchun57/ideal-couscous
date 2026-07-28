const api = require('../../services/api.js');

const STATUS = {
  open: '待处置', dispatched: '已派单', verified: '已核实',
  resolved: '已解决', archived: '已归档',
};
const TYPE = {
  sensory: '感官异常', equipment: '设备异常', environment: '环境异常',
  violation: '违规操作', pollution: '污染事件',
};

Page({
  data: { loading: true, reports: [], filter: '', filters: ['全部', '待处理', '已核实', '已解决', '已归档'] },

  onShow() { this.load(); },

  load() {
    this.setData({ loading: true });
    api.manualReports().then((rows) => {
      const reports = (rows || []).map((row) => ({
        ...row,
        type_cn: TYPE[row.report_type] || row.report_type || '现场异常',
        status_cn: STATUS[row.status] || row.status || '待处理',
        photo_count: (() => { try { return JSON.parse(row.photo_urls || '[]').length; } catch (_) { return 0; } })(),
      }));
      this.setData({ reports, loading: false });
    }).catch(() => this.setData({ loading: false }));
  },

  onFilter(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const statuses = ['', 'dispatched', 'verified', 'resolved', 'archived'];
    this.setData({ filter: statuses[index] || '' });
  },

  onGoReport() { wx.switchTab({ url: '/pages/inspection/inspection' }); },

  goOrder(e) {
    const orderNo = e.currentTarget.dataset.order;
    if (!orderNo) return;
    getApp().globalData.selWorkorderNo = orderNo;
    wx.navigateTo({ url: '/pages/workorder/workorder' });
  },
});
