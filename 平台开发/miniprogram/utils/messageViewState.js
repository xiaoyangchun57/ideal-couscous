function hasMessageItems(list) {
  return Array.isArray(list) && list.length > 0;
}

function getMessageViewState(options) {
  const opts = options || {};
  const hasItems = hasMessageItems(opts.list);
  if (hasItems) return 'data';
  if (opts.loading || !opts.loaded) return 'loading';
  if (opts.error) return 'error';
  return 'empty';
}

function shouldShowNoMore(list, noMore) {
  return hasMessageItems(list) && !!noMore;
}

function shouldShowLoadMore(list, noMore, loading, error) {
  return hasMessageItems(list) && !noMore && !loading && !error;
}

function getMessageLoadError(error) {
  const raw = error && (error.error || error.message || error.errMsg);
  const message = String(raw || '加载失败').replace(/^request:fail\s*/, '').trim();
  return message || '加载失败';
}

module.exports = {
  getMessageViewState,
  hasMessageItems,
  shouldShowNoMore,
  shouldShowLoadMore,
  getMessageLoadError
};
