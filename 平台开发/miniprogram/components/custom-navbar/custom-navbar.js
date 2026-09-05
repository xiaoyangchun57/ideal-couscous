// components/custom-navbar/custom-navbar.js
// 自定义导航栏：适配微信胶囊按钮，支持紧凑/大标题模式
Component({
  options: {
    multipleSlots: true,
    styleIsolation: 'apply-shared'
  },

  properties: {
    // 是否显示底部分隔线
    showBorder: { type: Boolean, value: true }
  },

  data: {
    statusBarHeight: 20,
    navBarHeight: 44,
    // 右侧留给胶囊按钮的空间（胶囊宽度+右边距+间距）
    rightPadding: 104
  },

  lifetimes: {
    attached() {
      this._initNavBar()
    }
  },

  methods: {
    _initNavBar() {
      const sysInfo = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
      const statusBarHeight = sysInfo.statusBarHeight || 20

      let menuButton
      try {
        menuButton = wx.getMenuButtonBoundingClientRect()
      } catch (e) {
        menuButton = null
      }

      let navBarHeight = 44
      let rightPadding = 104

      if (menuButton && menuButton.width) {
        rightPadding = (sysInfo.windowWidth - menuButton.left) + 8
        navBarHeight = (menuButton.top - statusBarHeight) * 2 + menuButton.height
      }

      this.setData({
        statusBarHeight,
        navBarHeight,
        rightPadding
      })

      const totalHeight = statusBarHeight + navBarHeight
      this.triggerEvent('navready', { height: totalHeight, statusBarHeight, navBarHeight })
    }
  }
})
