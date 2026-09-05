const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const {
  errorMessage,
  formatHomeDate,
  projectHome,
  projectIdentity,
  projectReview,
  projectUnread
} = require('../../utils/homeTaskState.js');
const { currentUnreadRevision } = require('../../utils/notificationCount.js');
const {
  buildPackageOption,
  normalizeExecutionTarget,
  uniqueExecutableTarget,
} = require('../../utils/executionTarget.js');

function todayStr() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

const app = getApp();

Page({
  data: {
    displayName: '',
    greeting: '',
    dateLabel: '',
    canReview: false,

    mainState: 'initial_loading',
    mainError: '',
    actions: [],
    workPackage: null,

    notificationsState: 'loading',
    unreadCount: null,
    unreadDisplay: '',

    reviewVisible: false,
    reviewState: 'hidden',
    reviewCount: null,
    reviewDisplay: '',
  },

  onLoad() {
    this._alive = true;
    this._requestGeneration = { main: 0, notifications: 0, review: 0 };
    this._unreadRevision = currentUnreadRevision();
    this._navigationInFlight = false;
    this._hasMainData = false;
    this.setData(projectIdentity(getUser(), todayStr(), new Date().getHours()));
  },

  onShow() {
    if (!app.globalData.token) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    this._navigationInFlight = false;
    this._prepareUnreadRefresh();
    this.loadMain();
    this.loadNotifications();
    if (this.data.canReview) this.loadReview();
  },

  onUnload() {
    this._alive = false;
    this._navigationInFlight = false;
  },

  onPullDownRefresh() {
    const tasks = [this.loadMain(true)];
    tasks.push(this.loadNotifications(true));
    if (this.data.canReview) tasks.push(this.loadReview(true));

    Promise.all(tasks).then(() => wx.stopPullDownRefresh(), () => wx.stopPullDownRefresh());
  },

  _beginRequest(kind) {
    if (!this._requestGeneration) this._requestGeneration = { main: 0, notifications: 0, review: 0 };
    this._requestGeneration[kind] += 1;
    return this._requestGeneration[kind];
  },

  _isCurrentRequest(kind, generation) {
    return this._alive !== false && this._requestGeneration && this._requestGeneration[kind] === generation;
  },

  _prepareUnreadRefresh() {
    const revision = currentUnreadRevision();
    if (this._unreadRevision === revision) return;
    this._unreadRevision = revision;
    this.setData({ notificationsState: 'loading', unreadCount: null, unreadDisplay: '' });
  },

  loadMain(isRefresh) {
    const generation = this._beginRequest('main');
    const preserveExisting = !!this._hasMainData;
    if (this._alive !== false) {
      this.setData({ mainState: preserveExisting ? 'refreshing' : 'initial_loading', mainError: '' });
    }
    return api.myToday()
      .then(res => {
        if (!this._isCurrentRequest('main', generation)) return;
        const vm = projectHome(res);
        this._hasMainData = true;
        this.setData({ mainState: 'ready', mainError: '', actions: vm.actions, workPackage: vm.workPackage });
      })
      .catch(err => {
        if (!this._isCurrentRequest('main', generation)) return;
        this.setData({
          mainState: preserveExisting ? 'refresh_error' : 'blocking_error',
          mainError: errorMessage(err, '今日任务加载失败，请重试')
        });
      });
  },

  onRetryMain() {
    return this.loadMain(this._hasMainData);
  },

  loadNotifications(isRefresh) {
    const generation = this._beginRequest('notifications');
    const unreadRevision = currentUnreadRevision();
    return api.unreadCount()
      .then(res => {
        if (!this._isCurrentRequest('notifications', generation)
          || unreadRevision !== currentUnreadRevision()) return;
        this._unreadRevision = unreadRevision;
        this.setData(projectUnread(res));
      })
      .catch(() => {
        if (!this._isCurrentRequest('notifications', generation)
          || unreadRevision !== currentUnreadRevision()) return;
        this._unreadRevision = unreadRevision;
        this.setData({ notificationsState: 'unavailable', unreadCount: null, unreadDisplay: '' });
      });
  },

  loadReview(isRefresh) {
    if (!this.data.canReview) return Promise.resolve();
    const generation = this._beginRequest('review');
    if (this.data.reviewCount === null && this._alive !== false) {
      this.setData({ reviewState: 'loading', reviewDisplay: '加载中' });
    }
    return api.auditPending()
      .then(rows => {
        if (!this._isCurrentRequest('review', generation)) return;
        this.setData(projectReview(rows));
      })
      .catch(() => {
        if (!this._isCurrentRequest('review', generation)) return;
        this.setData({ reviewState: 'unavailable', reviewCount: null, reviewDisplay: '数量暂不可用' });
      });
  },

  _navigateLocked(method, url, onFailure) {
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      this._navigationInFlight = false;
    };
    const fail = () => {
      if (onFailure) onFailure();
      finish();
    };
    try {
      wx[method]({ url, fail, complete: finish });
    } catch (error) {
      fail();
    }
    return true;
  },

  _navigateOnce(method, url, onFailure) {
    if (this._navigationInFlight || this._alive === false) return false;
    this._navigationInFlight = true;
    return this._navigateLocked(method, url, onFailure);
  },

  goMessages() {
    return this._navigateOnce('navigateTo', '/pages/message/message');
  },

  goInspection() {
    if (!this.data.workPackage || !this.data.workPackage.hasPlan
        || this._navigationInFlight || this._alive === false) return false;
    this._navigationInFlight = true;
    const requestId = (this._inspectionTargetRequestId || 0) + 1;
    this._inspectionTargetRequestId = requestId;
    const navigate = target => {
      if (this._alive === false || requestId !== this._inspectionTargetRequestId) return false;
      app.globalData.executionTarget = target;
      return this._navigateLocked('navigateTo', '/pages/inspection/inspection', () => {
        if (app.globalData.executionTarget === target) app.globalData.executionTarget = null;
      });
    };
    return api.todayExecution().then(res => {
      const packages = (res && Array.isArray(res.packages) ? res.packages : [])
        .map(buildPackageOption);
      return navigate(uniqueExecutableTarget(packages, 'home'));
    }).catch(() => navigate(null));
  },

  goWorkorder() {
    if (this._navigationInFlight || this._alive === false) return false;
    // 清除精确工单目标，确保"全部工单"入口始终打开列表态
    app.globalData.selWorkorderNo = null;
    return this._navigateOnce('navigateTo', '/pages/workorder/workorder', () => {
      wx.showToast({ title: '打开工单列表失败，请重试', icon: 'none' });
    });
  },

  goReview() {
    if (!this.data.canReview) return;
    return this._navigateOnce('navigateTo', '/pages/review/view');
  },

  goPlan() {
    return this._navigateOnce('switchTab', '/pages/plan/plan');
  },

  onActionTap(e) {
    const idx = Number(e.currentTarget.dataset.index);
    const action = this.data.actions[idx];
    if (!action || !action.target) return false;

    if (action.target.kind === 'workorder') {
      const objectId = String(action.target.objectId || '').trim();
      if (!objectId || this._navigationInFlight || this._alive === false) return false;
      app.globalData.selWorkorderNo = objectId;
      return this._navigateOnce('navigateTo', '/pages/workorder/workorder', () => {
        if (app.globalData.selWorkorderNo === objectId) app.globalData.selWorkorderNo = null;
      });
    }
    if (action.target.kind === 'alert') {
      const objectId = Number(action.target.objectId);
      if (!Number.isFinite(objectId) || objectId <= 0 || this._navigationInFlight || this._alive === false) return false;
      app.globalData.selAlertId = objectId;
      return this._navigateOnce('switchTab', '/pages/alert/alert', () => {
        if (app.globalData.selAlertId === objectId) app.globalData.selAlertId = null;
      });
    }
    if (action.target.kind === 'inspection_rework') {
      if (this._navigationInFlight || this._alive === false) return false;
      const target = normalizeExecutionTarget(action.target);
      if (!target.siteId) return false;
      app.globalData.executionTarget = target;
      return this._navigateOnce('navigateTo', '/pages/inspection/inspection', () => {
        if (app.globalData.executionTarget === target) app.globalData.executionTarget = null;
      });
    }
    return false;
  },
});

module.exports = { formatHomeDate };
