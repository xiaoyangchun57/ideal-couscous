Component({
  properties: {
    // 类型：default / primary / success / warning / error / info
    type: {
      type: String,
      value: 'default'
    },
    // 变体：pill（胶囊）/ round（圆角矩形）
    variant: {
      type: String,
      value: 'round'
    },
    // 尺寸：sm / md
    size: {
      type: String,
      value: 'md'
    },
    // 文字
    text: {
      type: String,
      value: ''
    }
  },
  data: {},
  methods: {}
})
