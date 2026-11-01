import { useCallback, useEffect, useRef, useState } from 'react';

export const REPORT_EVIDENCE_STATE_KEY = '__reportEvidence';
export const REPORT_EVIDENCE_PREVIEW_STATE_KEY = '__reportEvidencePreview';

export function buildReportEvidenceState(currentState, token) {
  const baseState = currentState && typeof currentState === 'object' ? currentState : {};
  return { ...baseState, [REPORT_EVIDENCE_STATE_KEY]: token };
}

export function buildReportEvidencePreviewState(currentState, evidenceToken, previewToken) {
  return {
    ...buildReportEvidenceState(currentState, evidenceToken),
    [REPORT_EVIDENCE_PREVIEW_STATE_KEY]: previewToken,
  };
}

export function isReportEvidenceState(state, token) {
  return Boolean(token && state?.[REPORT_EVIDENCE_STATE_KEY] === token);
}

export function isReportEvidencePreviewState(state, token) {
  return Boolean(token && state?.[REPORT_EVIDENCE_PREVIEW_STATE_KEY] === token);
}

export function reportEvidencePreviewHistoryAction(nextVisible, currentState, token) {
  if (nextVisible) {
    return isReportEvidencePreviewState(currentState, token) ? 'show' : 'push';
  }
  return isReportEvidencePreviewState(currentState, token) ? 'back' : 'hide';
}

export function reportEvidenceHistoryLayer(state, evidenceToken, previewToken) {
  if (isReportEvidencePreviewState(state, previewToken)) return 'preview';
  if (isReportEvidenceState(state, evidenceToken)) return 'evidence';
  return 'list';
}

let previewSequence = 0;

function createPreviewToken(identity) {
  previewSequence += 1;
  return `report-evidence:${identity}:${Date.now()}:${previewSequence}`;
}

export function useReportEvidenceHistory() {
  const [evidenceRecord, setEvidenceRecord] = useState(null);
  const [previewVisible, setPreviewVisible] = useState(false);
  const recordRef = useRef(null);
  const evidenceTokenRef = useRef('');
  const previewTokenRef = useRef('');

  const applyLayer = useCallback((layer) => {
    setEvidenceRecord(layer === 'list' ? null : recordRef.current);
    setPreviewVisible(layer === 'preview');
  }, []);

  const openEvidence = useCallback((record) => {
    const identity = String(record?.id ?? 'unknown');
    recordRef.current = record;
    evidenceTokenRef.current = createPreviewToken(`evidence:${identity}`);
    previewTokenRef.current = createPreviewToken(`preview:${identity}`);
    window.history.pushState(
      buildReportEvidenceState(window.history.state, evidenceTokenRef.current),
      '',
      window.location.href,
    );
    applyLayer('evidence');
  }, [applyLayer]);

  const closeEvidence = useCallback(() => {
    if (isReportEvidenceState(window.history.state, evidenceTokenRef.current)) {
      window.history.back();
      return;
    }
    applyLayer('list');
  }, [applyLayer]);

  useEffect(() => {
    const handlePopState = (event) => {
      applyLayer(reportEvidenceHistoryLayer(
        event.state,
        evidenceTokenRef.current,
        previewTokenRef.current,
      ));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [applyLayer]);

  const onVisibleChange = useCallback((nextVisible) => {
    if (!recordRef.current) return;
    const action = reportEvidencePreviewHistoryAction(
      nextVisible,
      window.history.state,
      previewTokenRef.current,
    );
    if (action === 'push') {
      window.history.pushState(
        buildReportEvidencePreviewState(
          window.history.state,
          evidenceTokenRef.current,
          previewTokenRef.current,
        ),
        '',
        window.location.href,
      );
      applyLayer('preview');
      return;
    }
    if (action === 'back') {
      window.history.back();
      return;
    }
    applyLayer(nextVisible ? 'preview' : 'evidence');
  }, [applyLayer]);

  return {
    evidenceRecord,
    openEvidence,
    closeEvidence,
    preview: { visible: previewVisible, onVisibleChange },
  };
}
