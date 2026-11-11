export function createHealthRequest({ load, onStart, onSuccess, onError, onIdle }) {
  let active = null;
  let desired = null;
  let version = 0;
  let disposed = false;

  async function run(key, requestVersion) {
    try {
      const result = await load(key);
      if (!disposed && requestVersion === version) onSuccess(result, key);
    } catch (error) {
      if (!disposed && requestVersion === version) onError(error, key);
    } finally {
      active = null;
      if (!disposed && requestVersion !== version) {
        active = { key: desired, version };
        onStart(desired);
        run(desired, version);
      } else if (!disposed) {
        onIdle();
      }
    }
  }

  return {
    request(key) {
      if (disposed) return;
      if (active && desired === key) return;
      desired = key;
      version += 1;
      if (!active) {
        active = { key, version };
        onStart(key);
        run(key, version);
      }
    },
    dispose() { disposed = true; version += 1; },
  };
}
