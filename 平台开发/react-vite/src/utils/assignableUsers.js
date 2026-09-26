/**
 * 「可作为分配候选」的统一判定。
 *
 * 产品语义（Web 收口校准 · 人员退出）：人员退出 = 注销。已注销账号无登录权限，
 * 也不得再出现在登录、分配与候选列表中；历史业务记录仍可读（历史展示走服务端
 * 快照字段，不依赖本模块）。
 *
 * 判定依据分两层：
 * 1. 主判据 status === 'active'。停用与注销都会把 status 置为 inactive，
 *    因此这一层已同时排除「已注销」与「已停用」。
 * 2. 二次防御 deleted_at。后端注销路径当前会同时写 status='inactive' 与
 *    deleted_at（backend/app.py 的 DELETE /api/users/<uid>），两者始终同变；
 *    此处独立校验 deleted_at，仅用于防止将来某一侧只写其中一个字段时
 *    已注销账号重新漏进候选列表。
 */

const ASSIGNABLE_STATUS = 'active';

export function isAssignableUser(row) {
  if (!row || typeof row !== 'object') return false;
  if (row.status !== ASSIGNABLE_STATUS) return false;
  if (row.deleted_at) return false;
  return true;
}

export function roleListOf(row) {
  if (!row || typeof row !== 'object') return [];
  const roles = Array.isArray(row.roles) && row.roles.length ? row.roles : [row.role];
  return roles.filter(Boolean);
}

export function filterAssignableUsers(rows, options = {}) {
  const { role } = options;
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!isAssignableUser(row)) return false;
    if (!role) return true;
    return roleListOf(row).includes(role);
  });
}
