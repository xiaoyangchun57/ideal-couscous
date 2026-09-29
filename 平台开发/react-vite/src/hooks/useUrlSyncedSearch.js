import { useCallback, useEffect, useRef, useState } from 'react';

export function createUrlSearchDraftController({
  onDraft,
  onCommit,
  delay = 350,
  schedule = setTimeout,
  cancel = clearTimeout,
}) {
  let composing = false;
  let compositionCommitValue = null;
  let timer = null;

  const cancelPending = () => {
    if (timer !== null) cancel(timer);
    timer = null;
  };

  const commit = (value) => {
    cancelPending();
    onCommit(value);
  };

  return {
    change(value) {
      onDraft(value);
      if (composing) return;
      if (compositionCommitValue === value) {
        compositionCommitValue = null;
        return;
      }
      compositionCommitValue = null;
      if (value === '') {
        commit(value);
        return;
      }
      cancelPending();
      timer = schedule(() => {
        timer = null;
        onCommit(value);
      }, delay);
    },
    compositionStart() {
      composing = true;
      compositionCommitValue = null;
      cancelPending();
    },
    compositionEnd(value) {
      composing = false;
      compositionCommitValue = value;
      onDraft(value);
      commit(value);
    },
    syncExternal(value) {
      // 输入法选词（composition）进行中：外部 URL 回写不得覆盖正在选词的草稿，
      // 最终值由 compositionEnd 时的用户输入决定（中文搜索不被打断）。
      if (composing) return;
      compositionCommitValue = null;
      cancelPending();
      onDraft(value);
    },
    dispose: cancelPending,
  };
}

export function useUrlSyncedSearch(urlValue, onCommit, { delay = 350 } = {}) {
  const [draft, setDraft] = useState(urlValue || '');
  const commitRef = useRef(onCommit);
  const controllerRef = useRef(null);
  commitRef.current = onCommit;

  if (!controllerRef.current) {
    controllerRef.current = createUrlSearchDraftController({
      onDraft: setDraft,
      onCommit: (value) => commitRef.current(value),
      delay,
    });
  }

  useEffect(() => {
    controllerRef.current.syncExternal(urlValue || '');
  }, [urlValue]);

  useEffect(() => () => controllerRef.current.dispose(), []);

  const onChange = useCallback((event) => {
    controllerRef.current.change(event.target.value);
  }, []);
  const onCompositionStart = useCallback(() => {
    controllerRef.current.compositionStart();
  }, []);
  const onCompositionEnd = useCallback((event) => {
    controllerRef.current.compositionEnd(event.currentTarget.value);
  }, []);

  return {
    draft,
    inputProps: { value: draft, onChange, onCompositionStart, onCompositionEnd },
  };
}
