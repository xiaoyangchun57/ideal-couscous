Component({
  properties: {
    // 类型：empty（无数据）/ task（全部完成）/ error（加载失败）
    type: {
      type: String,
      value: 'empty'
    },
    // 主标题
    title: {
      type: String,
      value: '暂无数据'
    },
    // 副标题/描述
    description: {
      type: String,
      value: ''
    },
    // 操作按钮文字（为空不显示按钮）
    buttonText: {
      type: String,
      value: ''
    }
  },
  data: {},
  methods: {
    onButtonTap() {
      this.triggerEvent('buttontap')
    }
  }
})
