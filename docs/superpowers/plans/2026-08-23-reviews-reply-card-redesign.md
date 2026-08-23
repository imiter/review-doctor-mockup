# 리뷰 답글 카드 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리뷰 관리 화면의 답글 작성 진입점을 "직접 답글 쓰기"(AI 호출 없음)와 "AI 추천 답글 보기"(기존 실제 RAG/템플릿 API 재사용) 두 버튼으로 명시적으로 분리하고, "Mock"이라는 낡은 문구를 실제 동작을 설명하는 문구로 바꾼다.

**Architecture:** `frontend/src/app/(app)/reviews/page.tsx`의 `ReviewCard` 컴포넌트에 `mode`("idle" | "manual" | "ai") 상태를 추가해 답글 없는 리뷰의 렌더링을 상태 기계로 재구성한다. 백엔드 변경은 없다 — 기존 `POST /reviews/{id}/generate-reply`, `POST /reviews/{id}/reply` 엔드포인트를 그대로 재사용한다.

**Tech Stack:** Next.js/React/TypeScript(프론트엔드 단독 변경).

## Global Constraints

- 백엔드 변경 없음 — `/reviews/{id}/generate-reply`, `/reviews/{id}/reply` API의 요청/응답 형태를 그대로 쓴다. 새 엔드포인트를 만들지 않는다.
- 경쟁사 스크린샷의 시각 디자인(색상, "Pro" 배지, 요금제 업셀 문구)을 그대로 가져오지 않는다 — 이 프로젝트의 기존 다크 테마·컴포넌트 스타일(`text-accent`, `bg-accent`, `border-border-subtle`, `rounded-lg` 등 이미 파일에서 쓰이는 클래스)로 재구성한다.
- 이 프로젝트 프론트엔드에는 자동화 테스트가 없다(`npm test` 스크립트 없음). 검증은 `npm run build`(타입 체크)와 `npm run lint`, 그리고 dev 서버에서의 직접 확인으로 한다.
- 저장 시 `style_id`는 AI 추천 경로로 작성했을 때만 실제 스타일 id를, 직접 쓴 경우는 `null`을 보낸다 — 백엔드 `SaveReplyRequest.style_id: int | None = None`이 이미 이를 지원한다(수정 불필요).
- "다시 생성"은 사용자가 마지막 생성 결과를 편집한 상태라면 실행 전 `window.confirm`으로 한 번 확인한다(되돌릴 방법이 없으므로).

---

### Task 1: 리뷰 카드 답글 작성 UI 재설계 + Mock 문구 정리

**Files:**
- Modify: `frontend/src/app/(app)/reviews/page.tsx`

**Interfaces:**
- Consumes: 기존 `apiPost<{content: string}>("/reviews/{id}/generate-reply", {style_id})`, `apiPost("/reviews/{id}/reply", {style_id, content})` — 둘 다 이미 존재하고 시그니처 변경 없음.
- Produces: 없음 (이 플랜의 유일한 태스크).

이 태스크는 자동화 테스트가 없으므로 TDD 사이클(실패하는 테스트 → 구현 → 통과) 대신 "구현 → 빌드/린트 확인 → 수동 체크리스트 확인 → 커밋" 순서로 진행한다.

- [ ] **Step 1: `ReviewCard` 컴포넌트 전체를 아래 코드로 교체**

`frontend/src/app/(app)/reviews/page.tsx`에서 `function ReviewCard({...`부터 그 함수의 닫는 `}`까지(현재 114번째 줄 `function ReviewCard({` 부터 288번째 줄 `}`까지) 전체를 아래 코드로 통째로 교체한다:

```tsx
function ReviewCard({
  review, styles, onSaved, brandName,
}: {
  review: Review; styles: ReplyStyle[]; onSaved: () => void; brandName?: string;
}) {
  const { refreshBilling } = useStoreContext();
  const [mode, setMode] = useState<"idle" | "manual" | "ai">(review.draft_reply ? "ai" : "idle");
  const [styleId, setStyleId] = useState(review.draft_reply?.style_id ?? styles[0]?.id ?? 0);
  const [draft, setDraft] = useState(review.draft_reply?.content ?? "");
  const [lastGenerated, setLastGenerated] = useState(review.draft_reply?.content ?? "");
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [secondaryText, setSecondaryText] = useState("");
  const [savingSecondary, setSavingSecondary] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  useEffect(() => {
    if (styles.length > 0 && styleId === 0) setStyleId(styles[0].id);
  }, [styles, styleId]);

  const generate = async () => {
    setGenerating(true);
    setGenerateError(null);
    try {
      const res = await apiPost<{ content: string }>(`/reviews/${review.id}/generate-reply`, { style_id: styleId });
      setDraft(res.content);
      setLastGenerated(res.content);
      setMode("ai");
      // onSaved()를 여기서 부르지 않는다 — 목록을 새로고침하면 이 리뷰가
      // 서버에서 이미 pending으로 바뀌어 "답글 대기" 필터에서 사라지고,
      // 사장님이 미리보기를 확인·수정하기도 전에 카드가 없어져 버린다.
      // 목록 갱신은 최종 "답글 등록"을 눌렀을 때만 한다.
      await refreshBilling();
    } catch (e) {
      if (e instanceof ApiError && e.errorCode === "reply_limit_exceeded") {
        setGenerateError(e.message);
      } else {
        setGenerateError(e instanceof ApiError ? e.message : "답글 생성에 실패했습니다.");
      }
    } finally {
      setGenerating(false);
    }
  };

  const regenerate = async () => {
    // 마지막 생성 결과와 지금 텍스트가 다르면(직접 손을 댄 흔적이 있으면)
    // 되돌릴 방법이 없으니 실수로 날리지 않도록 한 번 확인한다.
    if (draft !== lastGenerated) {
      const confirmed = window.confirm("지금까지 수정한 내용이 사라집니다. 다시 생성할까요?");
      if (!confirmed) return;
    }
    await generate();
  };

  const startManual = () => {
    setGenerateError(null);
    setMode("manual");
    setDraft("");
  };

  const cancelDraft = () => {
    setMode("idle");
    setDraft("");
    setGenerateError(null);
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPost(`/reviews/${review.id}/reply`, { style_id: mode === "ai" ? styleId : null, content: draft });
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const saveSecondary = async () => {
    if (!secondaryText.trim()) return;
    setSavingSecondary(true);
    try {
      await apiPost(`/reviews/${review.id}/secondary-reply`, { content: secondaryText });
      setSecondaryText("");
      onSaved();
    } finally {
      setSavingSecondary(false);
    }
  };

  return (
    <div className="rounded-xl border border-border-subtle bg-surface-2 p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="rounded bg-surface px-2 py-0.5 font-medium text-accent">{review.platform_name}</span>
        {brandName && (
          <span className="rounded bg-surface px-2 py-0.5 font-medium text-foreground">{brandName}</span>
        )}
        {review.category !== "no_issue" && (
          <span
            className={`rounded bg-surface px-2 py-0.5 font-medium ${
              review.is_sensitive ? "text-danger" : "text-warning"
            }`}
          >
            {review.is_sensitive ? "⚠ " : ""}
            {CATEGORY_LABELS[review.category] ?? review.category}
          </span>
        )}
        <span className="text-warning">{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span>
        <span className="font-medium text-foreground">{review.customer_nickname}</span>
        <span>· {review.customer_order_count}회 주문</span>
        <span className="ml-auto">{new Date(review.created_at).toLocaleString("ko-KR")}</span>
      </div>
      <p className="mt-2 text-xs text-muted">{review.menu_summary}</p>
      <p className="mt-1 text-sm text-foreground">{review.content}</p>

      {review.final_reply ? (
        <div className="mt-3 space-y-2">
          <div className="rounded-lg border border-border-subtle bg-surface p-3">
            <p className="mb-1 text-xs font-medium text-success">등록된 답글</p>
            <p className="text-sm text-foreground">{review.final_reply.content}</p>
          </div>
          {review.secondary_replies.map((r) => (
            <div key={r.id} className="rounded-lg border border-accent/30 bg-accent-soft p-3">
              <p className="mb-1 text-xs font-medium text-accent">2차 답글</p>
              <p className="text-sm text-foreground">{r.content}</p>
            </div>
          ))}
          <div className="flex gap-2">
            <input
              value={secondaryText}
              onChange={(e) => setSecondaryText(e.target.value)}
              placeholder="추가로 안내할 내용을 입력하세요"
              className="flex-1 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
            <button
              onClick={saveSecondary}
              disabled={savingSecondary || !secondaryText.trim()}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {savingSecondary ? "등록 중..." : "2차 답글 등록"}
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          {mode === "idle" && (
            <div className="flex flex-wrap gap-2">
              <button
                onClick={startManual}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs font-medium text-muted transition hover:text-foreground"
              >
                직접 답글 쓰기
              </button>
              <button
                onClick={generate}
                disabled={generating}
                className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {generating ? "생성 중..." : "✨ AI 추천 답글 보기"}
              </button>
            </div>
          )}

          {generateError && (
            <div className="space-y-2">
              <p className="text-xs text-danger">
                {generateError}{" "}
                <Link href="/account/billing" className="underline">
                  구독 관리
                </Link>
              </p>
              {mode === "idle" && (
                <button
                  onClick={startManual}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs font-medium text-muted transition hover:text-foreground"
                >
                  대신 직접 답글 쓰기
                </button>
              )}
            </div>
          )}

          {mode === "ai" && (
            <div className="space-y-2 rounded-lg border border-accent/40 bg-accent-soft/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-medium text-accent">AI 추천 답글</p>
                <div className="flex items-center gap-2">
                  <select
                    value={styleId}
                    onChange={(e) => setStyleId(Number(e.target.value))}
                    className="rounded-lg border border-border-subtle bg-surface px-2 py-1 text-xs outline-none focus:border-accent"
                  >
                    {styles.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                  <button
                    onClick={regenerate}
                    disabled={generating}
                    title="다시 생성"
                    className="rounded-lg border border-border-subtle p-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                  >
                    {generating ? "..." : "↻"}
                  </button>
                </div>
              </div>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-border-subtle bg-surface p-2.5 text-sm outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={save}
                  disabled={saving || !draft.trim()}
                  className="rounded-lg bg-success px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "등록 중..." : "이대로 답글 등록"}
                </button>
                <button
                  onClick={cancelDraft}
                  disabled={saving}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                >
                  취소
                </button>
              </div>
            </div>
          )}

          {mode === "manual" && (
            <div className="space-y-2 rounded-lg border border-border-subtle bg-surface p-3">
              <p className="text-xs font-medium text-muted">직접 작성</p>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                autoFocus
                placeholder="답글을 입력하세요"
                className="w-full rounded-lg border border-border-subtle bg-surface-2 p-2.5 text-sm outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={save}
                  disabled={saving || !draft.trim()}
                  className="rounded-lg bg-success px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "등록 중..." : "이대로 답글 등록"}
                </button>
                <button
                  onClick={cancelDraft}
                  disabled={saving}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted transition hover:text-foreground disabled:opacity-50"
                >
                  취소
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

이 교체로 실제로 바뀌는 부분: `mode`/`lastGenerated` 상태 추가, `regenerate`/`startManual`/`cancelDraft` 함수 추가, `save`의 `style_id` 인자가 `mode === "ai" ? styleId : null`로 바뀜, 답글 없는 리뷰의 렌더링이 버튼 2개(`mode === "idle"`) → AI 패널(`mode === "ai"`) 또는 수동 패널(`mode === "manual"`) 상태 기계로 재구성됨. 헤더(플랫폼 배지~작성일시), `review.final_reply` 블록(등록된 답글 + 2차 답글), `saveSecondary` 로직은 기존 그대로다.

- [ ] **Step 2: 페이지 상단 안내 문구 수정**

같은 파일에서 `ReviewsPage` 컴포넌트의 return 안, 다음 줄을 찾는다:

```tsx
        <p className="text-sm text-muted">답글 생성은 스타일 템플릿 기반 Mock — 실제 AI 호출 없음</p>
```

다음으로 교체한다:

```tsx
        <p className="text-sm text-muted">칭찬 리뷰는 스타일 템플릿으로, 불만 리뷰는 실제 AI가 사장님 말투를 학습해 답글을 생성해요</p>
```

- [ ] **Step 3: 빌드로 타입 오류 확인**

Run: `cd frontend && npm run build`
Expected: 에러 없이 빌드 성공. 실패하면 Step 1에서 교체한 코드의 상태 변수명(`mode`, `lastGenerated`)과 JSX 안 참조가 일치하는지 다시 확인한다.

- [ ] **Step 4: 린트 확인**

Run: `cd frontend && npm run lint`
Expected: 이 파일에서 새로 생긴 에러가 없어야 한다(이 프로젝트에 이미 있는 `react-hooks/set-state-in-effect` 계열 경고가 다른 파일들에 이미 여러 건 있다 — 이 태스크가 건드리지 않은 기존 경고는 무시해도 된다. `frontend/src/app/(app)/reviews/page.tsx` 자체에 새 에러/경고가 생겼는지만 확인).

- [ ] **Step 5: dev 서버에서 직접 확인**

Run: `cd frontend && npm run dev` (백엔드도 `ANTHROPIC_API_KEY`가 설정된 상태로 함께 떠 있어야 실제 AI 호출까지 확인 가능)

아래를 실제로 클릭해보며 확인한다:
- 답글 없는 리뷰 카드에 "직접 답글 쓰기"와 "✨ AI 추천 답글 보기" 버튼 2개가 나란히 보이는지
- "직접 답글 쓰기" 클릭 → 브라우저 개발자도구 네트워크 탭에서 `/generate-reply` 호출이 발생하지 않는지, 빈 텍스트 영역이 바로 열리는지
- "✨ AI 추천 답글 보기" 클릭 → 칭찬 리뷰(별점 4~5, 불만 없음)와 불만 리뷰(배달 지연 등) 둘 다에서 실제로 다른 성격의 답글이 채워지는지(칭찬은 템플릿, 불만은 RAG)
- AI 패널에서 텍스트를 고친 뒤 "↻"(다시 생성) 클릭 → "지금까지 수정한 내용이 사라집니다..." 확인창이 뜨는지, 취소하면 그대로 남고 확인하면 새로 생성되는지
- 텍스트를 고치지 않은 채 바로 "↻" 클릭 → 확인창 없이 바로 재생성되는지
- 두 경로(직접/AI) 모두 "이대로 답글 등록" 클릭 → 정상 저장되고 카드가 "등록된 답글" 상태로 바뀌는지
- 답글 생성 한도를 초과한 계정(또는 임시로 Basic 플랜 계정에서 10건 소진 후)으로 "AI 추천 답글 보기" 클릭 → 에러 메시지 + "대신 직접 답글 쓰기" 버튼이 뜨는지, 그 버튼을 눌러 수동 작성으로 전환되는지
- 페이지 상단 안내 문구가 "칭찬 리뷰는 스타일 템플릿으로, 불만 리뷰는 실제 AI가..."로 바뀌어 보이는지

- [ ] **Step 6: 커밋**

```bash
git add "frontend/src/app/(app)/reviews/page.tsx"
git commit -m "feat: 리뷰 답글 카드를 직접 쓰기/AI 추천 보기로 분리하고 Mock 문구 정리"
```

---

## Self-Review 메모 (플랜 작성자용, 실행 시 참고만)

- **스펙 커버리지**: 설계 문서의 "직접 쓰기"/"AI 추천 답글 보기" 버튼 분리, `mode` 상태 기계, "다시 생성" + 편집 감지 확인창, 문구 수정, 에러 처리 시 수동 작성 전환, 테스트 계획(빌드/린트/수동 체크리스트) 전부 Task 1로 매핑됨. 비목표(백엔드 변경 없음, 페르소나 실제 반영은 다음 스펙, 이미지 첨부 제외)는 Global Constraints와 코드에서 명시적으로 지켜짐(예: `save`가 여전히 기존 엔드포인트만 호출).
- **플레이스홀더 스캔**: "TODO"/"나중에" 등 없음. `ReviewCard` 전체가 실행 가능한 완성 코드로 제시됨.
- **타입/시그니처 일관성**: `mode` 값("idle"/"manual"/"ai")이 상태 선언과 모든 JSX 조건문에서 동일한 3개 리터럴로 일관되게 쓰임. `lastGenerated`가 `generate()`에서 설정되고 `regenerate()`에서만 비교에 쓰여 다른 곳과 이름이 갈라지지 않음.
