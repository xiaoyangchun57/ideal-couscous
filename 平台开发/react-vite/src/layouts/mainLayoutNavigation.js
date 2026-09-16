import { matchPath } from 'react-router-dom';

export function mainLayoutLocation(pathname, routeMeta, navigationPaths) {
  const metaKey = Object.keys(routeMeta).sort((a, b) => b.length - a.length)
    .find((path) => matchPath({ path, end: true }, pathname))
    || (matchPath('/sites/:siteId', pathname) ? '/sites' : null);
  const selectedKey = metaKey && navigationPaths.slice().sort((a, b) => b.length - a.length)
    .find((path) => matchPath({ path, end: path === '/' }, pathname));
  return {
    metaKey,
    selectedKey: selectedKey || null,
    meta: routeMeta[metaKey] || { group: '页面导航', title: '页面不存在' },
  };
}
