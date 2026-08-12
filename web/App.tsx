import { useEffect, useMemo, useRef, useState } from 'react';
import type { DraftData, Pole, ScenarioData, ScenarioSummary } from '../shared/types';
import { calculatePoleMetrics, haversineKm } from '../shared/geo';
import { POPULATION_BANDS } from '../shared/population';
import { api, type PopulationResponse, type RegionalOfficesResponse } from './api';
import MapView from './MapView';
import { areaGradient, poleColor } from '../shared/mapColors';

const fmt=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:0});
const km=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1});
type Snapshot={poles:ScenarioData['poles'];units:ScenarioData['units']};

export default function App(){
  const importInput=useRef<HTMLInputElement>(null);
  const [config,setConfig]=useState({mapboxToken:'',mapboxStyle:'mapbox://styles/mapbox/dark-v11'}),[scenarios,setScenarios]=useState<ScenarioSummary[]>([]),[draftRefs,setDraftRefs]=useState<any[]>([]);
  const [active,setActive]=useState(''),[data,setData]=useState<ScenarioData|null>(null),[draft,setDraft]=useState<DraftData|null>(null),[selectedPole,setSelectedPole]=useState<string|null>(null),[selectedUnits,setSelectedUnits]=useState(new Set<string>()),[selectedArea,setSelectedArea]=useState<string|null>(null),[showAll,setShowAll]=useState(false),[showPopulation,setShowPopulation]=useState(false),[population,setPopulation]=useState<PopulationResponse|null>(null),[showRegionals,setShowRegionals]=useState(false),[regionals,setRegionals]=useState<RegionalOfficesResponse|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [past,setPast]=useState<Snapshot[]>([]),[future,setFuture]=useState<Snapshot[]>([]),[filter,setFilter]=useState('');
  const refreshLists=async()=>{const [s,d]=await Promise.all([api.scenarios(),api.drafts()]);setScenarios(s);setDraftRefs(d);setActive(current=>current||s[0]?.id||d[0]?.id||'');};
  useEffect(()=>{Promise.all([api.config(),refreshLists()]).then(([c])=>setConfig(c as any)).catch(e=>setError(e.message));},[]);
  useEffect(()=>{if(!active)return;setLoading(true);setError('');const ref=draftRefs.find(d=>d.id===active);(ref?api.draft(active):active==='current'?api.current():api.scenario(active)).then((x:any)=>{if(ref){setDraft(x);setData(x.data);}else{setDraft(null);setData(x);}setSelectedPole(null);setSelectedArea(null);setSelectedUnits(new Set());setPast([]);setFuture([]);}).catch(e=>setError(e.message)).finally(()=>setLoading(false));},[active,draftRefs.length]);
  const selected=data?.poles.find(p=>p.id===selectedPole)||null;
  const metrics=selected&&data?calculatePoleMetrics(selected,data.units):null;
  const portfolio=useMemo(()=>selected&&data?data.units.filter(unit=>unit.poleId===selected.id).map(unit=>({unit,distance:haversineKm(selected.latitude,selected.longitude,unit.latitude,unit.longitude)})).sort((a,b)=>b.distance-a.distance):[],[data,selected]);
  const selectedFromPortfolio=portfolio.filter(({unit})=>selectedUnits.has(unit.id)).length;
  const toggleUnit=(id:string,additive=false)=>setSelectedUnits(old=>{
    const keepExisting=!!draft||additive;
    if(!keepExisting&&old.size===1&&old.has(id))return new Set();
    const next=new Set(keepExisting?old:[]);
    next.has(id)?next.delete(id):next.add(id);
    return next;
  });
  const pushHistory=()=>{if(!data)return;setPast(x=>[...x.slice(-39),{poles:structuredClone(data.poles),units:structuredClone(data.units)}]);setFuture([]);};
  const mutate=(fn:(d:ScenarioData)=>ScenarioData)=>{if(!data||!draft)return;pushHistory();const next=fn(structuredClone(data));setData(next);setDraft({...draft,data:next});};
  const movePole=(id:string,longitude:number,latitude:number)=>mutate(d=>{const p=d.poles.find(x=>x.id===id);if(p){p.longitude=longitude;p.latitude=latitude;}d.units.filter(u=>u.poleId===id).forEach(u=>u.distanceKm=haversineKm(latitude,longitude,u.latitude,u.longitude));return d;});
  const assign=()=>{if(!selectedPole||!selectedUnits.size)return;mutate(d=>{d.units.filter(u=>selectedUnits.has(u.id)).forEach(u=>{u.poleId=selectedPole;const p=d.poles.find(x=>x.id===selectedPole)!;u.distanceKm=haversineKm(p.latitude,p.longitude,u.latitude,u.longitude);});return d;});setSelectedUnits(new Set());};
  const removeFromPortfolio=()=>{if(!selectedPole||!selectedFromPortfolio)return;mutate(d=>{d.units.filter(u=>u.poleId===selectedPole&&selectedUnits.has(u.id)).forEach(u=>{u.poleId=null;u.distanceKm=0;});return d;});setSelectedUnits(new Set());};
  const redistribute=()=>{if(!selected||!selectedUnits.size)return;mutate(d=>{const candidates=d.poles.filter(p=>p.area===selected.area);d.units.filter(u=>selectedUnits.has(u.id)).forEach(u=>{const p=candidates.reduce((best,p)=>haversineKm(u.latitude,u.longitude,p.latitude,p.longitude)<haversineKm(u.latitude,u.longitude,best.latitude,best.longitude)?p:best,candidates[0]);u.poleId=p.id;u.distanceKm=haversineKm(u.latitude,u.longitude,p.latitude,p.longitude);});return d;});setSelectedUnits(new Set());};
  const undo=()=>{if(!data||!past.length)return;const prev=past.at(-1)!;setFuture(x=>[{poles:data.poles,units:data.units},...x]);const next={...data,poles:prev.poles,units:prev.units};setData(next);setDraft(d=>d?{...d,data:next}:d);setPast(x=>x.slice(0,-1));};
  const redo=()=>{if(!data||!future.length)return;const next=future[0];setPast(x=>[...x,{poles:data.poles,units:data.units}]);const nd={...data,poles:next.poles,units:next.units};setData(nd);setDraft(d=>d?{...d,data:nd}:d);setFuture(x=>x.slice(1));};
  const createBuilder=async()=>{if(!data)return;const name=`Builder — ${data.summary.name}`;const d=await api.createDraft(name,data.summary.id,{...structuredClone(data),summary:{...data.summary,id:'draft',name,kind:'draft'}});await refreshLists();setActive(d.id);};
  const save=async()=>{if(!draft||!data)return;try{const saved=await api.saveDraft({...draft,data});setDraft(saved);setError('');}catch(e:any){setError(e.message);}};
  const refreshCurrent=async()=>{try{await api.refresh();setError('Atualizando lojas ativas…');const poll=window.setInterval(async()=>{const status=await api.cacheStatus();if(status.refreshing)return;window.clearInterval(poll);if(status.lastError){setError(status.lastError);return;}await refreshLists();setActive('current');setError('');},2000);}catch(e:any){setError(e.message);}};
  const importDraft=async(file?:File)=>{if(!file)return;try{const source=JSON.parse(await file.text()) as DraftData;const created=await api.createDraft(`${source.name} — importado`,source.baseScenarioId,source.data);await refreshLists();setActive(created.id);}catch(e:any){setError(`Arquivo inválido: ${e.message}`);}finally{if(importInput.current)importInput.current.value='';}};
  const togglePopulation=async()=>{
    if(showPopulation){setShowPopulation(false);return;}
    setShowPopulation(true);
    if(population)return;
    const fallback:Record<string,number>={};
    data?.units.forEach(unit=>{fallback[unit.municipalityCode]=Math.max(fallback[unit.municipalityCode]||0,unit.population||0);});
    if(Object.keys(fallback).length)setPopulation({source:'cenário em exibição',censusYear:null,count:Object.keys(fallback).length,values:fallback,cachedAt:new Date().toISOString(),stale:true});
    try{setPopulation(await api.population());setError('');}catch(e:any){if(!Object.keys(fallback).length)setShowPopulation(false);setError(`Mapa populacional: ${e.message}`);}
  };
  const toggleRegionals=async()=>{
    if(showRegionals){setShowRegionals(false);return;}
    setShowRegionals(true);
    if(regionals)return;
    try{setRegionals(await api.regionalOffices());setError('');}catch(e:any){setShowRegionals(false);setError(`Gerências regionais: ${e.message}`);}
  };
  const areas=useMemo(()=>Object.entries(data?.summary.areaCounts||{}).filter(([a])=>a.toLowerCase().includes(filter.toLowerCase())),[data,filter]);
  const options=[...scenarios.map(s=>({id:s.id,label:`${s.kind==='current'?'Atual':s.kind.toUpperCase()} · ${s.name}`})),...draftRefs.map(d=>({id:d.id,label:`Builder · ${d.name}`}))];
  return <main className="shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">T</span><div><strong>Territórios BE</strong><small>Planejamento de cobertura</small></div></div>
      <select value={active} onChange={e=>setActive(e.target.value)} aria-label="Cenário">{options.map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select>
      <div className="top-actions"><button className={showPopulation?'heat-active':''} onClick={togglePopulation}>{showPopulation?'Ocultar população':'Mapa de calor'}</button><button className={showRegionals?'regional-active':''} onClick={toggleRegionals}>{showRegionals?`Ocultar regionais (${regionals?.count||0})`:'Gerências regionais'}</button><button className={showAll?'primary':''} onClick={()=>setShowAll(x=>!x)}>{showAll?'Ocultar carteiras':'Mostrar todas as áreas'}</button><button onClick={refreshCurrent}>Atualizar lojas</button><button onClick={()=>importInput.current?.click()}>Importar</button><input ref={importInput} hidden type="file" accept="application/json,.json" onChange={e=>importDraft(e.target.files?.[0])}/>{!draft&&data&&<button className="accent" onClick={createBuilder}>Abrir no Builder</button>}{draft&&<><button disabled={!past.length} onClick={undo}>↶</button><button disabled={!future.length} onClick={redo}>↷</button><a className="button-link" href={`/api/drafts/${draft.id}/export`}>JSON</a><a className="button-link" href={`/api/drafts/${draft.id}/geojson`}>GeoJSON</a><button className="accent" onClick={save}>Salvar rascunho</button></>}</div>
    </header>
    <section className="workspace"><aside className="legend-panel"><div className="panel-title"><div><small>AGRUPAMENTO</small><h2>Gerências de área</h2></div><b>{selectedArea?data?.poles.filter(pole=>pole.area===selectedArea).length:data?.poles.length||0}</b></div><input placeholder="Filtrar área…" value={filter} onChange={e=>setFilter(e.target.value)}/><div className="area-list">{areas.map(([a,n])=><button key={a} className={selectedArea===a?'active':''} onClick={()=>{setSelectedArea(current=>current===a?null:a);setSelectedPole(null);setSelectedUnits(new Set());}}><i style={{background:areaGradient(a,data?.poles||[])}}/><span>{a}</span><b>{n}</b></button>)}</div>{selectedArea&&<button className="clear-area" onClick={()=>setSelectedArea(null)}>Mostrar todas as gerências</button>}<div className="hint"><span>●</span><div><b>{draft?'Modo edição ativo':'Modo exploração'}</b><small>{draft?'Arraste polos ou selecione territórios.':'Clique em um polo para ver sua carteira.'}</small></div></div></aside>
      <div className="map-wrap">{config.mapboxToken?<MapView data={data} token={config.mapboxToken} styleUrl={config.mapboxStyle} selectedPole={selectedPole} selectedUnits={selectedUnits} selectedArea={selectedArea} showAll={showAll} showPopulation={showPopulation} population={population?.values||{}} showRegionals={showRegionals} regionals={regionals?.points||[]} editable={!!draft} onPole={setSelectedPole} onUnit={toggleUnit} onMovePole={movePole} onBoxSelect={ids=>setSelectedUnits(new Set(ids))}/>:<div className="empty"><h2>Token Mapbox ausente</h2><p>Adicione MAPBOX_ACCESS_TOKEN ao arquivo .env para carregar o mapa.</p></div>}{showPopulation&&<div className="population-legend"><h3>População municipal</h3><small>Brasil{population?.censusYear?` · Censo ${population.censusYear}`:''}</small>{POPULATION_BANDS.map(band=><div key={band.label}><i style={{background:band.color}}/><span>{band.label}</span></div>)}{population?.stale&&<em>Exibindo o último dado disponível</em>}</div>}{showRegionals&&regionals?.stale&&<div className="regional-cache-note">Regionais: último dado disponível</div>}{loading&&<div className="loading">Carregando cenário…</div>}{error&&<div className="toast" onClick={()=>setError('')}>{error}</div>}</div>
      <aside className="detail-panel">{selected&&metrics?<>
        <div className="selection-head"><span style={{background:poleColor(selected,data?.poles||[])}}/><div><small>POLO SELECIONADO</small><h2>{selected.name}</h2><p>{selected.area}{selected.uf?` · ${selected.uf}`:''}</p></div></div>
        <div className="metrics"><Metric label="Municípios" value={fmt.format(metrics.municipalities)}/><Metric label="Lojas" value={fmt.format(metrics.stores)}/><Metric label="População" value={fmt.format(metrics.population)}/><Metric label="Unidades" value={fmt.format(metrics.units)}/></div>
        <h3>Raio de atuação</h3><div className="radius"><div><small>MÍNIMO</small><b>{km.format(metrics.minKm)} km</b></div><div><small>MÉDIO</small><b>{km.format(metrics.meanKm)} km</b></div><div><small>MÁXIMO</small><b>{km.format(metrics.maxKm)} km</b></div></div>
        {draft&&<div className="builder-actions"><h3>Seleção territorial</h3><p>{selectedUnits.size} unidade(s) selecionada(s). Clique novamente para desmarcar.</p><button disabled={!selectedUnits.size} className="accent" onClick={assign}>Atribuir ao polo</button><button disabled={!selectedFromPortfolio} className="danger" onClick={removeFromPortfolio}>Retirar da carteira ({selectedFromPortfolio})</button><button disabled={!selectedUnits.size} onClick={redistribute}>Redistribuir na mesma área</button><button disabled={!selectedUnits.size} className="clear-selection" onClick={()=>setSelectedUnits(new Set())}>Limpar seleção</button><small>No Builder, os cliques são cumulativos: selecione ou desmarque quantos municípios precisar.</small></div>}
        <div className="portfolio-list"><div className="portfolio-title"><h3>Carteira</h3><small>Maior distância primeiro</small></div>{portfolio.slice(0,100).map(({unit,distance})=><button key={unit.id} aria-pressed={selectedUnits.has(unit.id)} className={selectedUnits.has(unit.id)?'selected':''} onClick={()=>toggleUnit(unit.id)}><i className="portfolio-check">{selectedUnits.has(unit.id)?'✓':''}</i><span>{unit.municipalityName||unit.id}<small>{unit.type} · {unit.stores} lojas</small></span><b>{km.format(distance)} km</b></button>)}</div>
      </>:<div className="empty side"><h2>Selecione um polo</h2><p>As métricas e a carteira aparecerão aqui.</p></div>}</aside>
    </section>
  </main>;
}
function Metric({label,value}:{label:string;value:string}){return <div><small>{label}</small><b>{value}</b></div>}
