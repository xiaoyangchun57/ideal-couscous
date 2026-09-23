import { filterAssignableUsers } from '../../utils/assignableUsers.js';

/**
 * 站点负责人候选：在「可作为分配候选」的统一判定之上，再要求具备 operator 角色。
 * 统一判定同时排除已注销（deleted_at 非空）与已停用（status !== 'active'）账号。
 */
export function filterSiteManagerCandidates(rows) {
  return filterAssignableUsers(rows, { role: 'operator' });
}
