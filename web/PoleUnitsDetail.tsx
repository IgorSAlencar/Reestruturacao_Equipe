import { useMemo, useState } from 'react';
import { POPULATION_BANDS } from '../shared/population';
import type { Pole, TerritoryUnit } from '../shared/types';
import {
  buildUnitDetailRows, filterUnitRows, sortUnitRows, type PoleBandRow, type SortDir, type UnitSortKey,
} from '../shared/areaManagement';
import { areaColor } from '../shared/mapColors';
import { HeatPill, SortableTh } from './tableBits';

const fmt = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
const km = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });

export default function PoleUnitsDetail({
  pole, summary, units, ibgePopulation, areaNames, onBack, onViewOnMap,
}: {
  pole: Pole;
  summary: PoleBandRow;
  units: TerritoryUnit[];
  ibgePopulation: Record<string, number>;
  areaNames: string[];
  onBack: () => void;
  onViewOnMap: () => void;
}) {
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<UnitSortKey>('population');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const rows = useMemo(() => buildUnitDetailRows(pole, units, ibgePopulation), [pole, units, ibgePopulation]);
  const visible = useMemo(() => sortUnitRows(filterUnitRows(rows, query), sortKey, sortDir), [rows, query, sortKey, sortDir]);
  const onSort = (key: string) => {
    const next = key as UnitSortKey;
    if (next === sortKey) setSortDir(dir => dir === 'asc' ? 'desc' : 'asc');
    else { setSortKey(next); setSortDir(next === 'name' || next === 'type' || next === 'uf' ? 'asc' : 'desc'); }
  };

  return (
    <div className="area-panel-detail">
      <div className="area-panel-detail-head">
        <button type="button" className="area-panel-back" onClick={onBack}>← Voltar aos polos</button>
        <div className="area-panel-detail-title">
          <span className="area-chip-mini"><i style={{ background: areaColor(pole.area, areaNames) }}/>{pole.area}</span>
          <h3>{pole.name}</h3>
          <p>{[pole.uf, pole.municipalityName].filter(Boolean).join(' · ') || 'Polo comercial'}</p>
        </div>
        <button type="button" className="area-panel-map-btn" onClick={onViewOnMap}>Ver no mapa</button>
      </div>
      <div className="area-panel-detail-metrics">
        <Metric label="Unidades" value={fmt.format(summary.units)}/>
        <Metric label="Municípios" value={fmt.format(summary.municipalities)}/>
        <Metric label="Distritos" value={fmt.format(summary.districts)}/>
        <Metric label="Lojas" value={fmt.format(summary.stores)}/>
        <Metric label="População" value={fmt.format(summary.population)}/>
      </div>
      <div className="area-panel-detail-bands">
        {POPULATION_BANDS.map((band, index) => (
          <div key={band.label} className="area-panel-band-stat">
            <HeatPill value={summary.bandCounts[index] || 0} color={band.color}/>
            <small>{band.label}</small>
          </div>
        ))}
      </div>
      <div className="area-panel-toolbar area-panel-toolbar-detail">
        <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Buscar município, distrito, IBGE ou UF…" aria-label="Buscar unidades do polo"/>
        <small>{query ? `${visible.length}/${rows.length}` : `${rows.length} unidades`}</small>
      </div>
      <div className="area-panel-table-wrap">
        <table className="area-panel-table area-panel-units">
          <thead>
            <tr>
              <SortableTh label="Unidade" sortKey="name" current={sortKey} dir={sortDir} onSort={onSort}/>
              <SortableTh label="Tipo" sortKey="type" current={sortKey} dir={sortDir} onSort={onSort}/>
              <SortableTh label="Município" sortKey="parent" current={sortKey} dir={sortDir} onSort={onSort}/>
              <SortableTh label="UF" sortKey="uf" current={sortKey} dir={sortDir} onSort={onSort}/>
              <SortableTh label="IBGE" sortKey="ibge" current={sortKey} dir={sortDir} onSort={onSort}/>
              <SortableTh label="População" sortKey="population" current={sortKey} dir={sortDir} onSort={onSort} align="right"/>
              <SortableTh label="Lojas" sortKey="stores" current={sortKey} dir={sortDir} onSort={onSort} align="right"/>
              <SortableTh label="Distância" sortKey="distanceKm" current={sortKey} dir={sortDir} onSort={onSort} align="right"/>
            </tr>
          </thead>
          <tbody>
            {visible.map(row => {
              const band = POPULATION_BANDS[row.bandIndex];
              return (
                <tr key={row.id}>
                  <td className="pole-name"><strong>{row.name}</strong></td>
                  <td><span className={`type-badge ${row.type === 'DISTRITO' ? 'is-district' : 'is-mun'}`}>{row.type === 'DISTRITO' ? 'Distrito' : 'Município'}</span></td>
                  <td>{row.parentMunicipality || (row.type === 'MUNICIPIO' ? row.name : '—')}</td>
                  <td>{row.uf || '—'}</td>
                  <td className="is-mono">{row.ibge || '—'}</td>
                  <td className="is-num"><HeatPill value={row.population} color={band?.color || '#152338'} title={band?.label}/></td>
                  <td className="is-num">{fmt.format(row.stores)}</td>
                  <td className="is-num">{km.format(row.distanceKm)} km</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!visible.length && <p className="area-panel-empty">Nenhuma unidade encontrada.</p>}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><b>{value}</b></div>;
}
