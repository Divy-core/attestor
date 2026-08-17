'use client';

import { useState } from 'react';

import { ArmorEventRow, InjectionDiff } from '@/components/armor/InjectionDiff';
import { PlaneNote } from '@/components/trace/PlaneNote';
import { TraceTree } from '@/components/trace/TraceTree';
import { Card, CardHeader, EmptyState, Tabs } from '@/components/ui/primitives';
import type { AuditEvent } from '@/lib/api/client';

/**
 * The audit page's three views.
 *
 * Armor events live here as a tab rather than as their own route. That is the plan of record's
 * own drop order — `/armor` as a dedicated page is the first thing to cut, folding into
 * `/traces` — and taking it deliberately while ahead of schedule is better than taking it at 2am
 * on the 28th. It is also the more honest arrangement: a guardrail block IS an audit event, it is
 * written to the same append-only collection, and giving it a separate page implies a separate
 * subsystem that does not exist.
 */
export function TracePanels({
  events,
  armorEvents,
}: {
  events: AuditEvent[];
  armorEvents: AuditEvent[];
}) {
  const [tab, setTab] = useState<'trail' | 'armor' | 'planes'>('trail');
  const [selected, setSelected] = useState(0);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <Tabs
        tabs={[
          { id: 'trail' as const, label: 'Audit trail', count: events.length },
          { id: 'armor' as const, label: 'Guardrail blocks', count: armorEvents.length },
          { id: 'planes' as const, label: 'Both planes' },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === 'trail' ? (
        <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <CardHeader
            title="Compliance plane"
            meta="Grouped by question, because the question this answers is “why did we answer yes to Q112?”"
          />
          <TraceTree events={events} />
        </Card>
      ) : null}

      {tab === 'armor' ? (
        armorEvents.length === 0 ? (
          <Card>
            <EmptyState title="Nothing was blocked in this review">
              Guardrail events appear here when Model Armor refuses content — a prompt injection in
              a questionnaire cell, or a poisoned passage retrieved from the corpus. An empty list
              means nothing hostile was submitted, not that screening is off: every question is
              screened on ingress and every retrieved passage on egress.
            </EmptyState>
          </Card>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,4fr)_minmax(0,8fr)] gap-3">
            <Card className="flex min-h-0 flex-col overflow-hidden">
              <CardHeader title="Blocks" meta={`${armorEvents.length} in this review`} />
              <div className="min-h-0 flex-1 overflow-y-auto">
                {armorEvents.map((event, index) => (
                  <ArmorEventRow
                    key={event.event_id ?? index}
                    event={event}
                    selected={index === selected}
                    onSelect={() => setSelected(index)}
                  />
                ))}
              </div>
            </Card>
            <div className="min-h-0 overflow-y-auto">
              {armorEvents[selected] ? <InjectionDiff event={armorEvents[selected]} /> : null}
            </div>
          </div>
        )
      ) : null}

      {tab === 'planes' ? <PlaneNote eventCount={events.length} /> : null}
    </div>
  );
}
