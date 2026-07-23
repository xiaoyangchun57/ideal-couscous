/* global ResizeObserver */
import { useState, useRef, useLayoutEffect } from 'react';

// 动态测量表格可用高度，返回 [wrapRef, bodyHeight]
// - 将 wrapRef 挂在 <Table> 的直接父容器上（该容器需用 flex:1 / minHeight:0 撑满背景框）
// - bodyHeight 即 scroll.y 应设的值（已扣除表头高度）
// - headerOffset: 表头高度（size="small" 约 40px）；deps: 依赖变化时重新测量
// 作用：让表格精确填满背景卡片，不再因写死 calc(100vh - Npx) 而溢出/留白。
export function useTableAutoHeight({ headerOffset = 40, deps = [] } = {}) {
  const wrapRef = useRef(null);
  const [bodyHeight, setBodyHeight] = useState(undefined);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;

    let rafIds = [];
    const measure = () => {
      const h = el.getBoundingClientRect().height;
      // 如果容器尚未完成 flex 布局（高度为 0），不强行设成最小值，避免 Table 被压成 120px
      // 留空由 ResizeObserver 在真实高度确定后再触发；但如果长期处于 0，说明未渲染，保持 undefined 即可
      if (h > 0) {
        setBodyHeight(Math.max(120, Math.floor(h - headerOffset)));
      }
    };

    // 初次测量 + 双 raf 延后测量，确保浏览器 flex 布局已稳定
    measure();
    rafIds.push(requestAnimationFrame(() => {
      measure();
      rafIds.push(requestAnimationFrame(measure));
    }));

    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener('resize', measure);

    return () => {
      rafIds.forEach(id => cancelAnimationFrame(id));
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return [wrapRef, bodyHeight];
}
