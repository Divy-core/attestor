'use client';

import { useCallback, useEffect, useState } from 'react';

/**
 * The reviewer's name, remembered for the session.
 *
 * Until Phase 7 this was the literal string `console-operator`, under a comment claiming "a
 * real name, not `system`" — a comment asserting something the code did not do. The audit
 * trail therefore recorded every approval against a constant, which cannot answer "who
 * approved this", and that is the one question the trail exists for.
 *
 * `localStorage`, so it is asked once rather than per answer: a reviewer working through
 * forty held answers must not retype their name forty times. It is **not** authentication
 * and makes no claim to be — nothing verifies it, the same way nothing verifies the token
 * in `guard.py`. It is an attribution, and an attribution a person typed is strictly better
 * than a constant a developer typed.
 *
 * Lifted out of `ApprovalQueue` in Phase 8 because the thread's composer needs the same
 * name for the same reason: a question asked in the thread is appended to the audit trail
 * with the asker on it, and two components keeping two copies of "who is at this keyboard"
 * would eventually disagree.
 */
export const OPERATOR_KEY = 'attestor-operator';

export function useOperator(): [string, (next: string) => void] {
  const [operator, setOperator] = useState('');

  useEffect(() => {
    setOperator(window.localStorage.getItem(OPERATOR_KEY) ?? '');
  }, []);

  const update = useCallback((next: string) => {
    setOperator(next);
    window.localStorage.setItem(OPERATOR_KEY, next);
  }, []);

  return [operator, update];
}
