import { cx } from '@/components/ui/primitives';

/**
 * The fleet, drawn: three identities, three corpora, and the six edges that do not exist.
 *
 * SVG rather than three.js. The claim is a *shape* — a diagonal, not a mesh — and a
 * diagonal is legible in one glance at any size, in both themes, with no runtime, no
 * `prefers-reduced-motion` branch, and nothing to load. A rotating node graph would take
 * longer to read and say less.
 *
 * The refused edges are drawn dashed rather than omitted. A diagram showing only what is
 * permitted looks the same as a diagram of a system with no boundaries in it.
 */

const CORPORA = ['security', 'legal', 'engineering'] as const;

export function FleetDiagram({ departments }: { departments: string[] }) {
  // Fall back to the three that are deployed. A registry read that failed should not empty
  // the diagram, and these three are what `infra/iam/scope_agents.py` binds either way.
  const rows = departments.length > 0 ? departments : [...CORPORA];

  const rowY = (index: number) => 40 + index * 56;
  const width = 640;
  const leftX = 150;
  const rightX = 430;

  return (
    <svg
      viewBox={`0 0 ${width} ${rowY(rows.length - 1) + 44}`}
      role="img"
      aria-label={`Each department engine reads one corpus and is refused the other ${
        CORPORA.length - 1
      }.`}
      className="w-full text-muted"
    >
      {rows.map((department, row) =>
        CORPORA.map((corpus, column) => {
          const granted = department === corpus;
          return (
            <line
              key={`${department}-${corpus}`}
              x1={leftX}
              y1={rowY(row)}
              x2={rightX}
              y2={rowY(column)}
              className={cx(granted ? 'stroke-cited' : 'stroke-subtle')}
              strokeWidth={granted ? 1.5 : 1}
              strokeDasharray={granted ? undefined : '3 4'}
            />
          );
        }),
      )}

      {rows.map((department, row) => (
        <g key={`agent-${department}`}>
          <circle cx={leftX} cy={rowY(row)} r={4} className="fill-strong" />
          <text
            x={leftX - 14}
            y={rowY(row) + 4}
            textAnchor="end"
            className="fill-current font-mono text-xs"
          >
            {label(department)}
          </text>
        </g>
      ))}

      {CORPORA.map((corpus, column) => (
        <g key={`corpus-${corpus}`}>
          <rect
            x={rightX - 4}
            y={rowY(column) - 4}
            width={8}
            height={8}
            className="fill-strong"
          />
          <text x={rightX + 14} y={rowY(column) + 4} className="fill-current font-mono text-xs">
            corpus/{corpus}
          </text>
        </g>
      ))}
    </svg>
  );
}

function label(department: string): string {
  const name = department.trim() || 'unassigned';
  return `${name.charAt(0).toUpperCase()}${name.slice(1)}Agent`;
}
