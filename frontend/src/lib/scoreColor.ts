/**
 * backend/app/acos.py의 _score_from_acos와 동일한 경계값(90/80/70점).
 * 두 파일 중 하나만 바뀌면 UI 색상 구간과 실제 점수 산정 기준이
 * 어긋나므로, 경계값을 고칠 땐 반드시 양쪽을 같이 고친다.
 * (모바일 앱 src/theme/scoreColor.ts와도 동일하게 맞춘다.)
 */
export function scoreColor(score: number): string {
  if (score >= 90) return "text-success";
  if (score >= 80) return "text-accent";
  if (score >= 70) return "text-warning";
  return "text-danger";
}

/**
 * backend/app/acos.py의 _score_from_acos 경계값(10%/15%/25%)과 동일.
 * ACoS는 낮을수록 좋다 — scoreColor와 방향이 반대다.
 */
export function acosColor(acos: number): string {
  if (acos < 10) return "text-success";
  if (acos < 15) return "text-accent";
  if (acos < 25) return "text-warning";
  return "text-danger";
}
