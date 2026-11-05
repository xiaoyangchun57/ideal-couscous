/* global ResizeObserver */
import { useCallback, useLayoutEffect, useMemo, useState } from 'react';

// 动态测量表格可用高度，返回 [wrapRef, bodyHeight]
// - 将 wrapRef 挂在 <Table> 的直接父容器上（该容器需用 flex:1 / minHeight:0 撑满背景框）
// - bodyHeight 即 scroll.y 应设的值（已扣除表头高度）
// - headerOffset: 表头高度（size="small" 约 40px）；deps: 依赖变化时重新测量
// 作用：让表格精确填满背景卡片，不再因写死 calc(100vh - Npx) 而溢出/留白。
export function useTableAutoHeight({ headerOffset = 40, deps = [] } = {}) {
  const [element, setElement] = useState(null);
  const [bodyHeight, setBodyHeight] = useState(undefined);
  const dependencyKey = useMemo(() => JSON.stringify(deps), [deps]);
  const wrapRef = useCallback((node) => setElement(node), []);

  useLayoutEffect(() => {
    if (!element) return undefined;

    let rafIds = [];
    const measure = () => {
      const h = element.getBoundingClientRect().height;
      if (h > 0) {
        const tableHeader = element.querySelector('.ant-table-thead');
        const pagination = element.querySelector('.ant-table-pagination');
        const measuredHeader = tableHeader?.getBoundingClientRect().height || headerOffset;
        let paginationHeight = 0;
        if (pagination) {
          const style = window.getComputedStyle(pagination);
          paginationHeight = pagination.getBoundingClientRect().height
            + (Number.parseFloat(style.marginTop) || 0)
            + (Number.parseFloat(style.marginBottom) || 0);
        }
        setBodyHeight(Math.max(96, Math.floor(h - measuredHeader - paginationHeight - 2)));
      }
    };

    // 初次测量 + 双 raf 延后测量，确保浏览器 flex 布局已稳定
    measure();
    rafIds.push(requestAnimationFrame(() => {
      measure();
      rafIds.push(requestAnimationFrame(measure));
    }));

    const ro = new ResizeObserver(measure);
    ro.observe(element);
    const mutationObserver = new MutationObserver(() => {
      measure();
      const tableHeader = element.querySelector('.ant-table-thead');
      const pagination = element.querySelector('.ant-table-pagination');
      if (tableHeader) ro.observe(tableHeader);
      if (pagination) ro.observe(pagination);
    });
    mutationObserver.observe(element, { childList: true, subtree: true });
    window.addEventListener('resize', measure);

    return () => {
      rafIds.forEach(id => cancelAnimationFrame(id));
      ro.disconnect();
      mutationObserver.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [dependencyKey, element, headerOffset]);

  return [wrapRef, bodyHeight];
}
