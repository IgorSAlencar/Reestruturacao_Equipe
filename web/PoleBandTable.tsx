import { POPULATION_BANDS } from '../shared/population';
import type { PoleBandRow, PoleSortKey, SortDir } from '../shared/areaManagement';
import { areaColor } from '../shared/mapColors';
import { HeatPill, SortableTh } from './tableBits';

const fmt = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });

export default function PoleBandTable({
  rows, sortKey, sortDir, onSort, onSelect, areaNames,
}: {
  rows: PoleBandRow[];
  sortKey: PoleSortKey;
  sortDir: SortDir;
  onSort: (key: PoleSortKey) => void;
  onSelect: (poleId: string) => void;
  areaNames: string[];
}) {
  const sort = (key: string) => onSort(key as PoleSortKey);
  return (
    <div className="area-panel-table-wrap">
      <table className="area-panel-table">
        <thead>
          <tr>
            <SortableTh label="Polo" sortKey="name" current={sortKey} dir={sortDir} onSort={sort}/>
            <SortableTh label="Gerência" sortKey="area" current={sortKey} dir={sortDir} onSort={sort}/>
            <SortableTh label="UF" sortKey="uf" current={sortKey} dir={sortDir} onSort={sort}/>
            <SortableTh label="Unidades" sortKey="units" current={sortKey} dir={sortDir} onSort={sort} align="right"/>
            <SortableTh label="Municípios" sortKey="municipalities" current={sortKey} dir={sortDir} onSort={sort} align="right"/>
            <SortableTh label="Distritos" sortKey="districts" current={sortKey} dir={sortDir} onSort={sort} align="right"/>
            <SortableTh label="Lojas" sortKey="stores" current={sortKey} dir={sortDir} onSort={sort} align="right"/>
            {POPULATION_BANDS.map((band, index) => (
              <th key={band.label} className="is-num band-h">
                <button type="button" onClick={() => sort(`band:${index}`)} title={band.label}>
                  <i style={{ background: band.color }} aria-hidden="true"/>
                  <span>{band.label}</span>
                  <em>{sortKey === `band:${index}` ? (sortDir === 'asc' ? '▲' : '▼') : ''}</em>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.poleId} onClick={() => onSelect(row.poleId)} tabIndex={0}
              onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(row.poleId); } }}>
              <td className="pole-name"><strong>{row.poleName}</strong></td>
              <td><span className="area-chip-mini"><i style={{ background: areaColor(row.area, areaNames) }}/>{row.area}</span></td>
              <td>{row.uf || '—'}</td>
              <td className="is-num"><b>{fmt.format(row.units)}</b></td>
              <td className="is-num">{fmt.format(row.municipalities)}</td>
              <td className="is-num">{row.districts ? fmt.format(row.districts) : '—'}</td>
              <td className="is-num">{fmt.format(row.stores)}</td>
              {POPULATION_BANDS.map((band, index) => (
                <td key={band.label} className="is-num">
                  <HeatPill value={row.bandCounts[index] || 0} color={band.color} title={band.label}/>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <p className="area-panel-empty">Nenhum polo encontrado com esse filtro.</p>}
    </div>
  );
}
