import { useEffect, useMemo, useState } from 'react';
import { POPULATION_BANDS } from '../shared/population';
import type { ScenarioData } from '../shared/types';
import {
  areaWorkbookSheets, buildPoleBandRows, buildUnitDetailRowsForPoles, filterPoleRows, sortPoleRows, summarizePoleRows,
  type PoleSortKey, type SortDir,
} from '../shared/areaManagement';
import { areaColor } from '../shared/mapColors';
import { downloadAreaWorkbook } from './exportAreaWorkbook';
import PoleBandTable from './PoleBandTable';
import PoleUnitsDetail from './PoleUnitsDetail';
import { HeatPill } from './tableBits';

const fmt = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });

export default function AreaManagementPanel({
  data, ibgePopulation, selectedArea, scenarioName, onClose, onViewOnMap,
}: {
  data: ScenarioData;
  ibgePopulation: Record<string, number>;
  selectedArea: string | null;
  scenarioName: string;
  onClose: () => void;
  onViewOnMap: (poleId: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [areaFilter, setAreaFilter] = useState<string | null>(selectedArea);
  const [sortKey, setSortKey] = useState<PoleSortKey>('area');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [openPoleId, setOpenPoleId] = useState<string | null>(null);

  useEffect(() => { setAreaFilter(selectedArea); }, [selectedArea]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { if (openPoleId) setOpenPoleId(null); else onClose(); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [openPoleId, onClose]);

  const allRows = useMemo(() => buildPoleBandRows(data.poles, data.units, ibgePopulation), [data, ibgePopulation]);
  const areaNames = useMemo(() => [...new Set(allRows.map(row => row.area))].sort((a, b) => a.localeCompare(b, 'pt-BR')), [allRows]);
  const visible = useMemo(() => sortPoleRows(filterPoleRows(allRows, query, areaFilter), sortKey, sortDir), [allRows, query, areaFilter, sortKey, sortDir]);
  const totals = useMemo(() => summarizePoleRows(visible), [visible]);
  const openPole = data.poles.find(pole => pole.id === openPoleId) || null;
  const openSummary = allRows.find(row => row.poleId === openPoleId) || null;

  const onSort = (key: PoleSortKey) => {
    if (key === sortKey) setSortDir(dir => dir === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir(key === 'name' || key === 'area' || key === 'uf' ? 'asc' : 'desc'); }
  };

  const exportExcel = () => {
    const poleIds = new Set(visible.map(row => row.poleId));
    const units = buildUnitDetailRowsForPoles(data.poles, data.units, ibgePopulation, poleIds);
    const slug = scenarioName.replace(/[^\w\-]+/g, '_').replace(/^_|_$/g, '') || 'cenario';
    downloadAreaWorkbook(`gerencia-area-${slug}`, areaWorkbookSheets(visible, units));
  };

  return (
    <div className="area-panel-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="area-panel" role="dialog" aria-modal="true" aria-labelledby="area-panel-title">
        <header className="area-panel-head">
          <div>
            <small>GERÊNCIA DE ÁREA</small>
            <h2 id="area-panel-title">Painel de cobertura</h2>
            <p>{scenarioName}</p>
          </div>
          <div className="area-panel-head-actions">
            <button type="button" className="area-panel-excel" onClick={exportExcel} disabled={!visible.length}>Exportar Excel</button>
            <button type="button" className="area-panel-close" onClick={onClose} aria-label="Fechar painel">×</button>
          </div>
        </header>
        <div className="area-panel-kpis" aria-label="Totais do recorte visível">
          <Kpi label="Polos" value={fmt.format(totals.poles)}/>
          <Kpi label="Unidades" value={fmt.format(totals.units)}/>
          <Kpi label="Municípios" value={fmt.format(totals.municipalities)}/>
          {totals.districts > 0 && <Kpi label="Distritos" value={fmt.format(totals.districts)}/>}
          <Kpi label="Lojas" value={fmt.format(totals.stores)}/>
          {POPULATION_BANDS.map((band, index) => (
            <div key={band.label} className="area-panel-kpi area-panel-kpi-band">
              <HeatPill value={totals.bandCounts[index] || 0} color={band.color}/>
              <small>{band.label}</small>
            </div>
          ))}
        </div>
        {openPole && openSummary ? (
          <PoleUnitsDetail
            pole={openPole}
            summary={openSummary}
            units={data.units}
            ibgePopulation={ibgePopulation}
            areaNames={areaNames}
            onBack={() => setOpenPoleId(null)}
            onViewOnMap={() => onViewOnMap(openPole.id)}
          />
        ) : (
          <div className="area-panel-main">
            <div className="area-panel-toolbar">
              <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Filtrar polo, gerência ou UF…" aria-label="Filtrar polos"/>
              <div className="area-panel-areas" role="group" aria-label="Gerências de área">
                <button type="button" className={!areaFilter ? 'active' : ''} onClick={() => setAreaFilter(null)}>Todas</button>
                {areaNames.map(area => (
                  <button type="button" key={area} className={areaFilter === area ? 'active' : ''} onClick={() => setAreaFilter(areaFilter === area ? null : area)}>
                    <i style={{ background: areaColor(area, areaNames) }}/>{area}
                  </button>
                ))}
              </div>
              <small className="area-panel-hint">Clique em um polo para ver municípios e distritos.</small>
            </div>
            <PoleBandTable rows={visible} sortKey={sortKey} sortDir={sortDir} onSort={onSort} onSelect={setOpenPoleId} areaNames={areaNames}/>
          </div>
        )}
      </section>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return <div className="area-panel-kpi"><b>{value}</b><small>{label}</small></div>;
}
