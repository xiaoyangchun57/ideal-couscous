export async function runIndependentLoads(sources) {
  return Promise.all(sources.map(async (source) => {
    try {
      const value = await source.load();
      source.onSuccess?.(value);
      return { key: source.key, status: 'fulfilled', value };
    } catch (error) {
      source.onError?.(error);
      return { key: source.key, status: 'rejected', reason: error };
    } finally {
      source.onSettled?.();
    }
  }));
}
