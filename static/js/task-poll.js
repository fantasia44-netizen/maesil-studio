/**
 * Celery 워커로 넘어간 생성 작업의 완료 여부를 폴링하는 공용 헬퍼.
 * experience.html의 기존 폴링 루프(3초 간격, 최대 240초)와 동일한 동작을 공유.
 *
 * @param {string} statusUrl - 상태 조회 엔드포인트 (예: '/create/image/status/' + id)
 * @param {object} [opts]
 * @param {number} [opts.intervalMs=3000] - 폴링 간격(ms)
 * @param {number} [opts.maxSeconds=240]  - 최대 대기 시간(초)
 * @returns {Promise<object>} 'done' 상태의 최종 응답 JSON
 * @throws {Error} 'failed'/'error' 상태이거나 시간 초과 시
 */
async function pollTaskStatus(statusUrl, opts) {
  const intervalMs = (opts && opts.intervalMs) || 3000;
  const maxSeconds  = (opts && opts.maxSeconds)  || 240;
  let elapsed = 0;
  while (elapsed < maxSeconds) {
    await new Promise(r => setTimeout(r, intervalMs));
    elapsed += intervalMs / 1000;
    let data;
    try {
      const res = await fetch(statusUrl);
      data = await res.json();
    } catch (pollErr) {
      continue;   // 폴링 중 네트워크 에러는 무시하고 계속 시도
    }
    if (data.status === 'done') {
      return data;
    } else if (data.status === 'failed' || data.status === 'error') {
      throw new Error(data.message || '생성 중 오류가 발생했습니다.');
    }
    // status === 'generating' → 계속 대기
  }
  throw new Error('생성 시간이 초과되었습니다. 잠시 후 생성 이력에서 확인해 주세요.');
}
