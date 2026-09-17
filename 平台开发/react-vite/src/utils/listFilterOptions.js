export const ALL_LIST_FILTER_VALUE = '__all_list_filter__';

export function listFilterOptions(allLabel, options = []) {
  return [{ value: ALL_LIST_FILTER_VALUE, label: allLabel }, ...options];
}

export function listFilterValue(value) {
  return value === ALL_LIST_FILTER_VALUE ? undefined : value;
}
