const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const { rolesForUser } = require('../../utils/reviewAccess.js');
const {
  filterPlans,
  projectFavoritePlans,
  projectPlanCards
} = require('../../utils/planViewModel.js');

const app = getApp();

function errorMessage(error, fallback) {
  return String(error && (error.error || error.message) || fallback);
}

Page({
  data: {
    canSwitchTeam: false,
    canUseFavorites: false,
    mainState: 'initial_loading',
    mainError: '',
    scope: 'mine',
    pendingScope: null,
    filter: 'all',
    allPlans: [],
    visiblePlans: [],
    favoritesVisible: false,
    favoritesState: 'hidden',
    favorites: [],
    favoriteSheet: {
      open: false,
      selectedId: null,
      selectedIndex: 0,
      periodStart: '',
      sheetState: 'idle',
      errorMessage: ''
    }
  },

  onLoad() {
    this._alive = true;
    this._requestGeneration = { main: 0, scope: 0, favorites: 0 };
    this._scopeIntentGeneration = 0;
    this._actionInFlight = new Set();
    this._navigationInFlight = false;
    this._hasMainData = false;
  },

  onUnload() {
    this._alive = false;
    this._navigationInFlight = false;
    if (this._actionInFlight) this._actionInFlight.clear();
    this.setFavoriteSheetTabBarHidden(false);
  },

  onHide() { this.setFavoriteSheetTabBarHidden(false); },

  onShow() {
    if (!app.globalData.token) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    const roles = rolesForUser(getUser() || {});
    const isOperator = roles.includes('operator');
    const isAdmin = roles.includes('admin');
    const isReviewer = roles.includes('reviewer');
    this.setData({
      canSwitchTeam: isAdmin || isReviewer,
      canUseFavorites: isOperator,
      favoritesVisible: isOperator
    });
    this.setFavoriteSheetTabBarHidden(!!this.data.favoriteSheet.open);
    this.loadMain();
    if (isOperator) this.loadFavorites();
  },

  onPullDownRefresh() {
    const tasks = [this.loadMain(true)];
    if (this.data.canUseFavorites) tasks.push(this.loadFavorites(true));
    Promise.all(tasks).then(() => wx.stopPullDownRefresh(), () => wx.stopPullDownRefresh());
  },

  _beginRequest(kind) {
    if (!this._requestGeneration) this._requestGeneration = { main: 0, scope: 0, favorites: 0 };
    this._requestGeneration[kind] += 1;
    return this._requestGeneration[kind];
  },

  _isCurrentRequest(kind, generation) {
    return this._alive !== false && this._requestGeneration && this._requestGeneration[kind] === generation;
  },

  _beginAction(key) {
    if (this._alive === false) return false;
    if (!this._actionInFlight) this._actionInFlight = new Set();
    if (this._actionInFlight.has(key)) return false;
    this._actionInFlight.add(key);
    return true;
  },

  _endAction(key) {
    if (this._actionInFlight) this._actionInFlight.delete(key);
  },

  _navigateOnce(url) {
    if (this._alive === false || this._navigationInFlight) return false;
    this._navigationInFlight = true;
    let finished = false;
    const release = () => {
      if (finished) return;
      finished = true;
      this._navigationInFlight = false;
    };
    try {
      wx.navigateTo({ url, fail: release, complete: release });
    } catch (error) {
      release();
      return false;
    }
    return true;
  },

  setFavoriteSheetTabBarHidden(hidden) {
    if (this._favoriteSheetTabBarHidden === hidden) return;
    const method = hidden ? 'hideTabBar' : 'showTabBar';
    if (typeof wx !== 'undefined' && typeof wx[method] === 'function') wx[method]({ animation: false });
    this._favoriteSheetTabBarHidden = hidden;
  },

  _setPlans(rows, scope) {
    const allPlans = projectPlanCards(rows);
    return {
      scope,
      pendingScope: null,
      mainState: 'ready',
      mainError: '',
      allPlans,
      visiblePlans: filterPlans(allPlans, this.data.filter)
    };
  },

  loadMain(isRefresh) {
    const scope = this.data.scope;
    const generation = this._beginRequest('main');
    const scopeIntentGeneration = this._scopeIntentGeneration || 0;
    const preserveExisting = this._hasMainData === true;
    if (this._alive !== false) {
      this.setData({ mainState: preserveExisting ? 'refreshing' : 'initial_loading', mainError: '' });
    }
    return api.planSchedules(scope === 'team')
      .then(rows => {
        if (!this._isCurrentRequest('main', generation) || scopeIntentGeneration !== this._scopeIntentGeneration) return false;
        this._hasMainData = true;
        this.setData(this._setPlans(rows, scope));
        return true;
      })
      .catch(error => {
        if (!this._isCurrentRequest('main', generation) || scopeIntentGeneration !== this._scopeIntentGeneration) return false;
        this.setData({
          mainState: preserveExisting ? 'refresh_error' : 'blocking_error',
          mainError: errorMessage(error, '计划加载失败，请重试'),
          pendingScope: null
        });
        return false;
      });
  },

  onRetryMain() {
    return this.loadMain();
  },

  onFilterTap(event) {
    const filter = event.currentTarget.dataset.f;
    if (!['all', 'draft', 'active', 'done'].includes(filter)) return;
    this.setData({ filter, visiblePlans: filterPlans(this.data.allPlans, filter) });
  },

  onToggleScope() {
    if (!this.data.canSwitchTeam || this._alive === false) return;
    const currentOrPending = this.data.pendingScope || this.data.scope;
    const targetScope = currentOrPending === 'team' ? 'mine' : 'team';
    this.setData({ pendingScope: targetScope });
    this.loadScope(targetScope);
  },

  loadScope(targetScope) {
    const generation = this._beginRequest('scope');
    const scopeIntentGeneration = (this._scopeIntentGeneration || 0) + 1;
    this._scopeIntentGeneration = scopeIntentGeneration;
    const preserveExisting = this._hasMainData === true;
    if (this._alive !== false) this.setData({ mainState: preserveExisting ? 'refreshing' : 'initial_loading', mainError: '' });
    return api.planSchedules(targetScope === 'team')
      .then(rows => {
        if (!this._isCurrentRequest('scope', generation) || scopeIntentGeneration !== this._scopeIntentGeneration) return false;
        this._beginRequest('main');
        this._hasMainData = true;
        this.setData(this._setPlans(rows, targetScope));
        return true;
      })
      .catch(error => {
        if (!this._isCurrentRequest('scope', generation) || scopeIntentGeneration !== this._scopeIntentGeneration) return false;
        this.setData({
          mainState: preserveExisting ? 'refresh_error' : 'blocking_error',
          mainError: errorMessage(error, '切换范围失败，请重试'),
          pendingScope: null
        });
        return false;
      });
  },

  onNewPlan() {
    return this._navigateOnce('/pages/plan-edit/plan-edit');
  },

  _findPlan(id) {
    const targetId = Number(id);
    return (this.data.allPlans || []).find(item => item.id === targetId) || null;
  },

  onItemTap(event) {
    const plan = this._findPlan(event.currentTarget.dataset.id);
    if (!plan || !plan.primaryTarget || !plan.primaryTarget.objectId) return false;
    const url = plan.primaryTarget.kind === 'edit'
      ? '/pages/plan-edit/plan-edit?id=' + plan.primaryTarget.objectId
      : '/pages/plan-detail/plan-detail?id=' + plan.primaryTarget.objectId;
    return this._navigateOnce(url);
  },

  onSecondaryAction(event) {
    const plan = this._findPlan(event.currentTarget.dataset.id);
    const action = event.currentTarget.dataset.action;
    if (!plan || action !== plan.secondaryAction) return false;
    if (action === 'delete_draft') return this.onDeleteDraft(plan.id);
    if (action === 'edit' || action === 'continue_change') {
      return this._navigateOnce('/pages/plan-edit/plan-edit?id=' + plan.id);
    }
    return false;
  },

  onDeleteDraft(id) {
    const plan = this._findPlan(id);
    const actionKey = 'delete-draft:' + id;
    if (!plan || plan.secondaryAction !== 'delete_draft' || !this._beginAction(actionKey)) return false;
    wx.showModal({
      title: '删除草稿',
      content: '仅删除未提交的草稿，确定继续？',
      confirmColor: '#ff3b30',
      success: result => {
        if (!result.confirm) {
          this._endAction(actionKey);
          return;
        }
        api.deletePlanSchedule(id)
          .then(() => {
            if (this._alive !== false) {
              wx.showToast({ title: '草稿已删除', icon: 'success' });
              this.loadMain(true);
            }
          })
          .catch(error => {
            if (this._alive !== false) wx.showToast({ title: errorMessage(error, '删除失败'), icon: 'none' });
          })
          .finally(() => this._endAction(actionKey));
      },
      fail: () => this._endAction(actionKey)
    });
    return true;
  },

  loadFavorites(isRefresh) {
    if (!this.data.canUseFavorites) return Promise.resolve(false);
    const generation = this._beginRequest('favorites');
    if (this._alive !== false && !isRefresh) this.setData({ favoritesState: 'loading' });
    return api.planScheduleFavorites()
      .then(rows => {
        if (!this._isCurrentRequest('favorites', generation)) return false;
        this.setData({ favoritesState: 'ready', favorites: projectFavoritePlans(rows) });
        return true;
      })
      .catch(() => {
        if (!this._isCurrentRequest('favorites', generation)) return false;
        this.setData({ favoritesState: 'unavailable' });
        return false;
      });
  },

  onOpenFavorites() {
    if (!this.data.canUseFavorites || this._navigationInFlight) return false;
    const favorites = this.data.favorites || [];
    if (!favorites.length) {
      const title = this.data.favoritesState === 'loading'
        ? '常用计划加载中，请稍候'
        : (this.data.favoritesState === 'unavailable' ? '常用计划暂不可用，请下拉刷新重试' : '暂无常用计划');
      wx.showToast({ title, icon: 'none' });
      return false;
    }
    const first = favorites[0];
    this.setData({
      'favoriteSheet.open': true,
      'favoriteSheet.selectedId': first.id,
      'favoriteSheet.selectedIndex': 0,
      'favoriteSheet.periodStart': first.suggestedPeriodStart || '',
      'favoriteSheet.sheetState': 'idle',
      'favoriteSheet.errorMessage': ''
    });
    this.setFavoriteSheetTabBarHidden(true);
    return true;
  },

  onCloseFavorites() {
    if (this.data.favoriteSheet.sheetState === 'submitting') {
      wx.showToast({ title: '草稿正在创建中，请稍候', icon: 'none' });
      return false;
    }
    this.setData({ 'favoriteSheet.open': false });
    this.setFavoriteSheetTabBarHidden(false);
    return true;
  },

  onFavoritePick(event) {
    const index = Number(event.detail.value) || 0;
    const favorite = (this.data.favorites || [])[index];
    if (!favorite) return;
    this.setData({
      'favoriteSheet.selectedIndex': index,
      'favoriteSheet.selectedId': favorite.id,
      'favoriteSheet.periodStart': favorite.suggestedPeriodStart || this.data.favoriteSheet.periodStart
    });
  },

  onFavoriteDate(event) {
    this.setData({ 'favoriteSheet.periodStart': event.detail.value });
  },

  onCreateFavoriteDraft() {
    const sheet = this.data.favoriteSheet;
    const actionKey = 'create-favorite-draft';
    if (!sheet.selectedId || !sheet.periodStart) {
      wx.showToast({ title: '请选择常用计划和开始日期', icon: 'none' });
      return false;
    }
    if (!this._beginAction(actionKey)) return false;
    this.setData({ 'favoriteSheet.sheetState': 'submitting', 'favoriteSheet.errorMessage': '' });
    api.createDraftFromPlanScheduleFavorite(sheet.selectedId, sheet.periodStart)
      .then(result => {
        const scheduleId = Number(result && result.schedule && result.schedule.id);
        if (!Number.isFinite(scheduleId) || scheduleId <= 0) throw new Error('草稿创建结果无效');
        if (this._alive !== false) {
          this.setData({ 'favoriteSheet.open': false, 'favoriteSheet.sheetState': 'idle' });
          this.setFavoriteSheetTabBarHidden(false);
          this._navigateOnce('/pages/plan-edit/plan-edit?id=' + scheduleId);
        }
      })
      .catch(error => {
        if (this._alive !== false) {
          this.setData({
            'favoriteSheet.sheetState': 'error',
            'favoriteSheet.errorMessage': errorMessage(error, '生成草稿失败，请重试')
          });
        }
      })
      .finally(() => this._endAction(actionKey));
    return true;
  },

  onDeleteFavorite() {
    const sheet = this.data.favoriteSheet;
    const actionKey = 'delete-favorite:' + sheet.selectedId;
    if (!sheet.selectedId || !this._beginAction(actionKey)) return false;
    wx.showModal({
      title: '删除常用计划',
      content: '仅删除收藏模板，不影响原计划和已有草稿。',
      confirmColor: '#ff3b30',
      success: result => {
        if (!result.confirm) {
          this._endAction(actionKey);
          return;
        }
        this.setData({ 'favoriteSheet.sheetState': 'submitting', 'favoriteSheet.errorMessage': '' });
        api.deletePlanScheduleFavorite(sheet.selectedId)
          .then(() => {
            if (this._alive !== false) {
              this.setData({ 'favoriteSheet.open': false, 'favoriteSheet.sheetState': 'idle' });
              this.setFavoriteSheetTabBarHidden(false);
              this.loadFavorites();
            }
          })
          .catch(error => {
            if (this._alive !== false) {
              this.setData({
                'favoriteSheet.sheetState': 'error',
                'favoriteSheet.errorMessage': errorMessage(error, '删除失败，请重试')
              });
            }
          })
          .finally(() => this._endAction(actionKey));
      },
      fail: () => this._endAction(actionKey)
    });
    return true;
  }
});
