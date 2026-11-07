import { useState } from 'react';

/**
 * 客户端列表分页（只给 pageSize / showSizeChanger，没有 current + onChange）必须由
 * 组件自己记住页码和每页条数。否则写死的 pageSize 会被 antd 当成受控值，
 * 用户切到 50/100 后一次重渲染就被覆盖回 20，看起来像「页条数选项无用」。
 *
 * 这里改成 defaultCurrent / defaultPageSize + 内部状态，让 antd 自己管理页码与
 * 边界（例如筛选后总页数变少会自动收敛），同时把当前值暴露给高度计算和无障碍播报。
 *
 * 服务端分页（带 current 或 onChange）完全交回调用方，不做任何改写。
 */
export function useClientPagination(pagination) {
  const isObject = Boolean(pagination) && typeof pagination === 'object';
  const isControlled = isObject && (
    typeof pagination.onChange === 'function'
    || Object.prototype.hasOwnProperty.call(pagination, 'current')
  );
  const [clientPage, setClientPage] = useState(() => ({
    current: Number(isObject ? (pagination.current || 1) : 1),
    pageSize: Number(isObject ? (pagination.pageSize || 20) : 20),
  }));

  if (!isObject || isControlled) {
    return {
      config: pagination,
      current: Number(isObject ? (pagination.current || 1) : 1),
      pageSize: Number(isObject ? (pagination.pageSize || 20) : 20),
    };
  }

  const rest = { ...pagination };
  delete rest.current;
  delete rest.pageSize;
  return {
    config: {
      ...rest,
      defaultCurrent: clientPage.current,
      defaultPageSize: clientPage.pageSize,
      onChange: (next, size) => setClientPage({ current: next, pageSize: size }),
    },
    current: clientPage.current,
    pageSize: clientPage.pageSize,
  };
}
