# Uber Learn — QA Test Matrix

Design reference: `/Users/awais.ansari/uber/docs/architecture/uber-learn-progress-api-solution-design.md`

All endpoint tests that require real views are marked `skipTest` until views are wired (step 4 of the sequencing plan). Remove the `skipTest()` call and re-run once the view is available.

---

## Test Files

| File | Focus | Run condition |
|---|---|---|
| `test_scoring.py` | Activity scoring business rules (AC-ACT-*) | Requires edx-platform env + migrations |
| `test_idempotency.py` | Idempotency and thread-safety (AC-IDEM-*, AC-CONC-*) | Requires edx-platform env + TransactionTestCase |
| `test_assessment_retry.py` | Assessment retry/cooldown state machine (AC-ASSESS-*) | Requires edx-platform env + migrations |
| `test_api.py` | API endpoints, auth, response shapes, DnD/Sortable/security (AC-API-*, AC-BADGE-*, AC-STREAK-*, etc.) | Most tests skip until views are wired |

---

## Auth / Enrollment (B.0)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Valid session, enrolled | 200 on GET /progress | AC-API-02 | Yes (skipTest) | P0 |
| 2 | No session | 401 on all endpoints | AC-API-01 | Yes (skipTest) | P0 |
| 3 | Valid session, not enrolled | 403 `not_enrolled` | AC-API-03 | Yes (skipTest) | P0 |
| 4 | Valid session, enrolled, flag off | 404 `feature_disabled` | — | Yes (skipTest) | P0 |
| 5 | Expired JWT | 401 | — | Manual | P0 |
| 6 | Staff user (not enrolled) | 403 (no staff bypass in v1) | — | Yes (skipTest) | P1 |

---

## Activity Scoring (B.2, C.1, LD-1)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Video: first completion | +10 pts, newly_completed=True | AC-ACT-01 | Yes | P0 |
| 2 | Video: duplicate completion | +0 pts, newly_completed=False | AC-ACT-02 | Yes | P0 |
| 3 | Reading: first completion | +10 pts | AC-ACT-03 | Yes | P0 |
| 4 | Reading: duplicate | 0 pts | AC-ACT-04 | Yes | P0 |
| 5 | Resource: first completion | +10 pts | AC-ACT-05 | Yes | P0 |
| 6 | Choice (CAPA): first incorrect | 0 pts, completed=False | AC-ACT-06 | Yes | P0 |
| 7 | Choice: incorrect then correct | **+10 pts on correct** (CRITICAL) | AC-ACT-07 | Yes | P0 |
| 8 | Choice: first correct | +10 pts | AC-ACT-08 | Yes | P0 |
| 9 | Choice: duplicate correct | 0 pts | AC-ACT-09 | Yes | P0 |
| 10 | Choice: 5 incorrect then correct | +10 pts total | AC-ACT-10 | Yes | P0 |
| 11 | Drag (DnD): incorrect | 0 pts | AC-ACT-11 | Yes | P0 |
| 12 | Drag: incorrect then correct | +10 pts | AC-ACT-12 | Yes | P0 |
| 13 | Sort: incorrect then correct | +10 pts | AC-ACT-13 | Yes | P0 |
| 14 | 5 distinct activities (all correct) | +50 pts total | AC-ACT-14 | Yes | P0 |
| 15 | Incorrect submissions total | 0 pts | AC-ACT-15 | Yes | P0 |
| 16 | Unknown activity type | ValueError raised | AC-ACT-17 | Yes | P0 |
| 17 | Deprecated slug 'capa' | ValueError (use 'choice') | AC-ACT-18 | Yes | P0 |
| 18 | Deprecated slug 'dnd' | ValueError (use 'drag') | AC-ACT-18 | Yes | P0 |
| 19 | Activity inside assessment vertical | 409 activity_is_assessment | AC-ACT-API-05 | Yes (skipTest) | P0 |
| 20 | client correct=True but server_correct=False | 0 pts (server wins) | AC-ACT-API-09 | Yes (contract test) | P0 |
| 21 | Completion is STICKY: later incorrect keeps completed_at | completed=True | — | Yes | P0 |

---

## Idempotency / Concurrency (G.1)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Network retry: same video POST twice | Exactly 10 pts total | AC-IDEM-01 | Yes | P0 |
| 2 | Network retry: same correct choice twice | Exactly 10 pts total | AC-IDEM-02 | Yes | P0 |
| 3 | 10 duplicate video POs | Exactly 10 pts total | AC-IDEM-03 | Yes | P0 |
| 4 | 5 threads concurrently completing same video | Exactly 10 pts total | AC-CONC-01 | Yes (TransactionTestCase) | P0 |
| 5 | 5 threads concurrently submitting correct choice | Exactly 10 pts total | AC-CONC-02 | Yes (TransactionTestCase) | P0 |
| 6 | Return type is (int, bool) | Not None | AC-IDEM-06 | Yes | P0 |

---

## Assessment Retry State Machine (D, B.3)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Baseline: 1st attempt | Allowed | AC-ASSESS-01 | Yes | P0 |
| 2 | Baseline: 10 consecutive attempts | All allowed (no cooldown) | AC-ASSESS-02 | Yes | P0 |
| 3 | Final: 1st attempt | 201 | AC-ASSESS-03 | Yes (skipTest) | P0 |
| 4 | Final: 2nd attempt | 201 | AC-ASSESS-04 | Yes (skipTest) | P0 |
| 5 | Final: 3rd attempt | 201 | AC-ASSESS-05 | Yes (skipTest) | P0 |
| 6 | Final: 4th attempt < 60s | 429 `cooldown_active` + Retry-After header | AC-ASSESS-06 | Yes (skipTest) | P0 |
| 7 | Final: 4th attempt >= 60s | **201 (NOT permanent lockout)** | AC-ASSESS-07 | Yes (skipTest) | P0 |
| 8 | Final: 7th attempt (two cooldowns expired) | 201 | AC-ASSESS-08 | Yes | P0 |
| 9 | Cooldown: wait_seconds range | 1 <= wait <= 60 | AC-ASSESS-09 | Yes | P0 |
| 10 | Attempt number increments | [1, 2, 3, ...] | AC-ASSESS-10 | Yes | P0 |
| 11 | Retention: same policy as final | Same as final tests | AC-ASSESS-11/12 | Yes | P0 |

---

## Server-Authoritative Assessment Scoring (E.1, E.2, C.3)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | POST /assessment has no score field | Request body: {assessment_type, idempotency_key} only | AC-ASSESS-REQ-01 | Yes | P0 |
| 2 | POST /assessment has no passed field | No passed in body | AC-ASSESS-REQ-03 | Yes | P0 |
| 3 | StudentModule: score=3/5 on final | passed=False | AC-ASSESS-API-02 | Yes (skipTest) | P0 |
| 4 | StudentModule: score=4/5 on final | passed=True | AC-ASSESS-API-03 | Yes (skipTest) | P0 |
| 5 | StudentModule: score=0/5 on baseline | passed=null (informational) | AC-ASSESS-18 | Yes | P0 |
| 6 | PASS_THRESHOLDS constants correct | final=0.8, retention=0.8, baseline=0.0 | AC-ASSESS-21 | Yes | P0 |
| 7 | Score > total | ValueError | AC-ASSESS-19 | Yes | P0 |
| 8 | record_assessment has no 'passed' param | inspect.signature check | AC-ASSESS-16 | Yes | P0 |

---

## Freshness Check (E.2)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Re-submit without re-answering questions | 409 `assessment_incomplete` with `missing_question_keys` | AC-FRESH-01 | Yes (skipTest) | P0 |
| 2 | Re-submit after re-answering all questions | 201, new attempt | AC-FRESH-02 | Yes (skipTest) | P0 |
| 3 | assessment_incomplete does NOT count as attempt | attempt_count unchanged | AC-FRESH-03 | Yes (skipTest) | P0 |

---

## Idempotency Key (G.2)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Same UUID twice → second call | 200 `replayed=True`, same attempt_number | AC-IDEM-KEY-01 | Yes (skipTest) | P0 |
| 2 | Same UUID, different assessment_type | 409 `idempotency_key_conflict` | AC-IDEM-KEY-02 | Yes (skipTest) | P0 |
| 3 | Missing idempotency_key | 400 `invalid_request` | AC-IDEM-KEY-03 | Yes (skipTest) | P0 |
| 4 | Non-UUID idempotency_key | 400 `invalid_request` | AC-IDEM-KEY-04 | Yes (skipTest) | P0 |
| 5 | Same UUID, different course | 409 `idempotency_key_conflict` | — | Manual | P0 |

---

## Badge Awards (C.6)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Final passed → applied badge | `badges_awarded_now` contains applied | AC-BADGE-01 | Yes (skipTest) | P0 |
| 2 | Final passed + all activities done → thorough badge | `badges_awarded_now` contains thorough | AC-BADGE-02 | Yes (skipTest) | P0 |
| 3 | Retention passed → retained badge | `badges_awarded_now` contains retained | AC-BADGE-03 | Yes (skipTest) | P0 |
| 4 | Activity completed after badge earned | Badge persists on GET /progress | AC-BADGE-04 | Yes (skipTest) | P0 |
| 5 | Final failed → NO applied badge | `badges_awarded_now` empty | AC-BADGE-05 | Yes (skipTest) | P0 |
| 6 | User retirement | All badge rows deleted | AC-RETIRE-03 | Yes (skipTest) | P0 |

---

## Streak (C.7, LD-8)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | First activity of the day | streak.current_days += 1 | AC-STREAK-01 | Yes (skipTest) | P0 |
| 2 | Re-completing same activity | streak unchanged | AC-STREAK-02 | Yes (skipTest) | P0 |
| 3 | Assessment submission | streak unchanged | AC-STREAK-03 | Yes (skipTest) | P0 |
| 4 | UTC midnight boundary | Correct day change | — | Manual | P1 |
| 5 | 2+ days gap | streak resets to 0 | — | Manual | P1 |

---

## GET /progress Response Shape (B.1)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Top-level fields present | course_id, server_time, points, activities, assessments, etc. | AC-PROG-01 | Yes | P0 |
| 2 | points sub-fields | earned, possible, per_activity | AC-PROG-02 | Yes | P0 |
| 3 | activities.items shape | usage_key, sequence_key, is_scorable, completed, etc. | AC-PROG-03 | Yes | P0 |
| 4 | Baseline passed is null | passed=null (informational, LD-5) | AC-PROG-04 | Yes | P0 |
| 5 | Assessment config sourced from /progress (not Course API) | usage_key, sequence_key, first_unit_key in response | AC-PROG-07 | Yes | P0 |
| 6 | other_course_settings NOT in Course API response | Must NOT call /api/courses/v1/courses/{id}/ for config | AC-PROG-07 | Yes (contract test) | P0 |

---

## DnD v2 Completion Modes (B.4)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Standard mode: finished=True | Emit plugin.completed | AC-DND-01 | Yes | P0 |
| 2 | Standard mode: finished=False | No plugin.completed | AC-DND-02 | Yes | P0 |
| 3 | Assessment mode: 'finished' key absent | Bridge must NOT check response.finished | AC-DND-03 | Yes | P0 |
| 4 | Assessment mode: correct=True | Emit plugin.completed | AC-DND-04 | Yes | P0 |
| 5 | Assessment mode: no attempts remain | Emit plugin.completed (correct=False OK) | AC-DND-05 | Yes | P0 |
| 6 | Assessment mode: correct=True, attempts remain | Still emit plugin.completed | AC-DND-06 | Yes | P0 |
| 7 | Returning user: state.finished=True | Emit plugin.completed on init | AC-DND-07 | Yes | P0 |
| 8 | Returning user: state.finished=False | No plugin.completed on init | AC-DND-08 | Yes | P0 |

---

## Sortable XBlock — Returning User (B.4)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | Server-rendered data-completed=true | Emit plugin.completed on page init | AC-SORT-01 | Yes | P0 |
| 2 | Server-rendered data-completed=false | No plugin.completed on init | AC-SORT-02 | Yes | P0 |
| 3 | Submit correct answer | Emit plugin.completed | AC-SORT-03 | Yes | P0 |
| 4 | Remaining_attempts=0 | Emit plugin.completed | AC-SORT-04 | Yes | P0 |
| 5 | Tutor sortable pin vs running branch | CI must use branch c44f189, not stale 0a77cda | — | **GAP: known** | P1 |

---

## Resume API (B.5)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | block_id key in response | Not 'block' | AC-RESUME-01 | Yes | P0 |
| 2 | section_id = sequential block | Not chapter, not 'section' | AC-RESUME-02/03 | Yes | P0 |
| 3 | unit_id key in response | Not 'unit' | AC-RESUME-04 | Yes | P0 |
| 4 | Fresh user (no position) | block_id=null, section_id=null, unit_id=null | — | Manual | P1 |

---

## Security / Edge Cases (B.4)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | plugin.resize message | Not treated as completion | AC-SEC-01 | Yes | P0 |
| 2 | Wrong origin postMessage | Silently ignored | AC-SEC-02 | Yes | P0 |
| 3 | Message without 'version' field | Silently ignored | AC-SEC-03 | Yes | P0 |
| 4 | event.data=null | No throw/crash | AC-SEC-04 | Yes | P0 |
| 5 | WebView request without Referer | Defaults to non-WebView, no 500 | AC-SEC-05 | Yes | P1 |

---

## User Retirement

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | USER_RETIRE_LMS_MISC signal | UberLearnActivityProgress rows deleted | AC-RETIRE-01 | Yes (skipTest) | P0 |
| 2 | USER_RETIRE_LMS_MISC signal | UberLearnAssessmentAttempt rows deleted | AC-RETIRE-02 | Yes (skipTest) | P0 |
| 3 | USER_RETIRE_LMS_MISC signal | UberLearnBadgeAward rows deleted | AC-RETIRE-03 | Yes (skipTest) | P0 |

---

## Retention Unlock (C.5, LD-6)

| # | Scenario | Expected | AC tag | Automated | Priority |
|---|---|---|---|---|---|
| 1 | retention before final passed | 409 `final_not_passed` | — | Yes (skipTest) | P0 |
| 2 | retention before 30d after final pass | 409 `retention_locked` with retention_unlocked_at | — | Yes (skipTest) | P0 |
| 3 | retention >= 30d after final pass | Allowed | — | Yes (skipTest) | P0 |
| 4 | retention_unlocked_at = final first pass + 30d UTC | Correct timestamp | — | Yes | P0 |

---

## Visual / A11y / Performance

| # | Scenario | Expected | Automated | Priority |
|---|---|---|---|---|
| 1 | 390px viewport | No overflow, design matches spec | Manual | P1 |
| 2 | VoiceOver navigation | Usable end-to-end | Manual | P1 |
| 3 | Keyboard navigation | All interactive elements reachable | Manual | P1 |
| 4 | LCP (course overview page) | < 2.5s | Lighthouse | P1 |
| 5 | Reduced-motion: animations skip | Motion vars respect prefers-reduced-motion | Manual | P1 |

---

## Known Gaps / Open Items

| Gap | Severity | Notes |
|---|---|---|
| Tutor Sortable XBlock pin is stale (0a77cda vs branch c44f189) | P1 | CI must pin to c44f189. Add this to the tutor-contrib-uber CI config. |
| Views not wired yet | BLOCKED | 57 tests currently skipTest. Unblock at sequencing step 4. |
| frontend-app-uber-learn MFE not scaffolded | BLOCKED | Frontend tests (usePostMessage, assessment-detect) cannot be written until MFE exists. |
| Tutor/CORS/CSRF: apps.{LMS_HOST} in trusted origins | P0 | Must be verified in tutor-contrib-uber before step 7 rollout. |
| H-1: One-attempt-per-60s vs fresh batch of 3 | P0 | Needs product sign-off. Current implementation: one per 60s after 3rd. |
| H-2: Can learner retake final after passing? | P0 | Needs product sign-off. Current: 409 already_passed. |
| StudentModule fallback for DnD/Sortable freshness | P1 | `StudentModule.modified` is used for non-CAPA freshness. Can treat a non-submit save as fresh. |
