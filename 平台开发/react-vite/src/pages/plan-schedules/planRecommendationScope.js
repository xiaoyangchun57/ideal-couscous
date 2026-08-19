export const DEFAULT_FOLLOW_UP_SCOPE = 'mine';

export function followUpRecommendationListPath(scope = DEFAULT_FOLLOW_UP_SCOPE) {
  return `/plan-schedules/follow-up-recommendations?scope=${scope === 'team' ? 'team' : 'mine'}`;
}

export function followUpRecommendationActionPayload(scope, item) {
  const payload = {
    user_id: item.user_id,
    site_id: item.site_id,
    anomaly_type: item.anomaly_type,
  };
  return scope === 'team' ? { ...payload, action: 'notify_owner' } : payload;
}

export async function loadFollowUpRecommendations(apiClient, scope, isCurrent = () => true) {
  try {
    const result = await apiClient.getStrict(followUpRecommendationListPath(scope));
    if (!isCurrent()) return null;
    return { followUpRecommendations: result?.recommendations || [], error: '' };
  } catch (error) {
    if (!isCurrent()) return null;
    return { followUpRecommendations: null, error: error?.message || '异常复查建议加载失败' };
  }
}
