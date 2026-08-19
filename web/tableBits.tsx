import { populationBandInk } from '../shared/population';

const fmt = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });

export function HeatPill({ value, color, empty = '—', title }: { value: number; color: string; empty?: string; title?: string }) {
  if (!value) return <span className="heat-pill heat-pill-empty" title={title}>{empty}</span>;
  return (
    <span className="heat-pill" title={title} style={{ background: color, color: populationBandInk(color) }}>
      {fmt.format(value)}
    </span>
  );
}

export function SortableTh({
  label, sortKey, current, dir, onSort, align = 'left', hint,
}: {
  label: string;
  sortKey: string;
  current: string;
  dir: 'asc' | 'desc';
  onSort: (key: string) => void;
  align?: 'left' | 'right';
  hint?: string;
}) {
  const active = current === sortKey;
  return (
    <th className={align === 'right' ? 'is-num' : undefined} title={hint || label} aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" onClick={() => onSort(sortKey)}>
        <span>{label}</span>
        <em>{active ? (dir === 'asc' ? '▲' : '▼') : ''}</em>
      </button>
    </th>
  );
}
