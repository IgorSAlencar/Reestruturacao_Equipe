import { useEffect, useMemo, useRef, useState } from 'react';
import type { DraftData, Pole, ScenarioData, ScenarioSummary } from '../shared/types';
import { calculatePoleMetrics, contiguousWithinRadius, haversineKm, mergeDuplicateMunicipalities, nearestMunicipalityPlace, normalizeMunicipalityCode, relocatePoleSeat, ufFromMunicipalityCode, uniqueUnitsByMunicipality, type MunicipalityPlace, type PoleHostMunicipality } from '../shared/geo';
import { POPULATION_BANDS } from '../shared/population';
import { api, type ExcludedMunicipalitiesResponse, type PopulationResponse, type RegionalOfficesResponse } from './api';
import MapView from './MapView';
import MunicipalitySizeBreakdown from './MunicipalitySizeBreakdown';
import PoleAreaTransfer from './PoleAreaTransfer';
import { areaColor, territoryColor } from '../shared/mapColors';
import { compareAreaCounts, countPolesByArea, matchPoleMovements, resolveAreaName } from '../shared/scenarioComparison';

const fmt=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:0});
const km=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1});
const pct=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1,minimumFractionDigits:1});
type Snapshot={poles:ScenarioData['poles'];units:ScenarioData['units']};

export default function App(){
  const importInput=useRef<HTMLInputElement>(null);
  const [config,setConfig]=useState({mapboxToken:'',mapboxStyle:'mapbox://styles/mapbox/dark-v11'}),[scenarios,setScenarios]=useState<ScenarioSummary[]>([]),[draftRefs,setDraftRefs]=useState<any[]>([]);
  const [active,setActive]=useState(''),[data,setData]=useState<ScenarioData|null>(null),[currentData,setCurrentData]=useState<ScenarioData|null>(null),[draft,setDraft]=useState<DraftData|null>(null),[selectedPole,setSelectedPole]=useState<string|null>(null),[selectedUnits,setSelectedUnits]=useState(new Set<string>()),[waveUnitId,setWaveUnitId]=useState<string|null>(null),[radiusKm,setRadiusKm]=useState(0),[selectedArea,setSelectedArea]=useState<string|null>(null),[showAll,setShowAll]=useState(false),[showPoles,setShowPoles]=useState(true),[showCurrentPoles,setShowCurrentPoles]=useState(false),[showMovementLines,setShowMovementLines]=useState(true),[showPopulation,setShowPopulation]=useState(false),[population,setPopulation]=useState<PopulationResponse|null>(null),[showRegionals,setShowRegionals]=useState(false),[regionals,setRegionals]=useState<RegionalOfficesResponse|null>(null),[showExcluded,setShowExcluded]=useState(false),[excludedMunicipalities,setExcludedMunicipalities]=useState<ExcludedMunicipalitiesResponse|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [past,setPast]=useState<Snapshot[]>([]),[future,setFuture]=useState<Snapshot[]>([]),[filter,setFilter]=useState(''),[portfolioQuery,setPortfolioQuery]=useState(''),[showMunicipalitySizes,setShowMunicipalitySizes]=useState(false);
  const [munCenters,setMunCenters]=useState<MunicipalityPlace[]>([]);
  const refreshLists=async()=>{const [s,d]=await Promise.all([api.scenarios(),api.drafts()]);setScenarios(s);setDraftRefs(d);setActive(current=>current||s[0]?.id||d[0]?.id||'');};
  useEffect(()=>{Promise.all([api.config(),refreshLists()]).then(([c])=>setConfig(c as any)).catch(e=>setError(e.message));},[]);
  useEffect(()=>{
    if(!draft)return;
    api.municipalityCenters()
      .then(res=>setMunCenters(res.places||[]))
      .catch(()=>setMunCenters([]));
  },[!!draft]);
  useEffect(()=>{
    if(!active)return;
    let cancelled=false;
    setLoading(true);setError('');setCurrentData(null);setShowCurrentPoles(false);setShowMovementLines(true);
    const ref=draftRefs.find(d=>d.id===active);
    const selectedRequest=ref?api.draft(active):active==='current'?api.current():api.scenario(active);
    const currentRequest=active==='current'?Promise.resolve(null):api.current().catch(()=>null);
    Promise.all([selectedRequest,currentRequest]).then(([x,current]:any[])=>{
      if(cancelled)return;
      if(ref){setDraft(x);setData(x.data);}else{setDraft(null);setData(x);}
      setCurrentData(current);setSelectedPole(null);setSelectedArea(null);setSelectedUnits(new Set());setWaveUnitId(null);setRadiusKm(0);setPortfolioQuery('');setShowMunicipalitySizes(false);setPast([]);setFuture([]);
    }).catch(e=>{if(!cancelled)setError(e.message);}).finally(()=>{if(!cancelled)setLoading(false);});
    return()=>{cancelled=true;};
  },[active,draftRefs.length]);
  const selected=data?.poles.find(p=>p.id===selectedPole)||null;
  const metrics=selected&&data?calculatePoleMetrics(selected,data.units):null;
  useEffect(()=>{setShowMunicipalitySizes(false);},[selectedPole]);
  const portfolio=useMemo(()=>{
    if(!selected||!data)return [];
    return uniqueUnitsByMunicipality(
      data.units.filter(unit=>unit.poleId===selected.id),
      selected.id,
    ).map(unit=>({unit,distance:haversineKm(selected.latitude,selected.longitude,unit.latitude,unit.longitude)}))
      .sort((a,b)=>b.distance-a.distance);
  },[data,selected]);
  const filteredPortfolio=useMemo(()=>{
    const q=portfolioQuery.trim().toLowerCase();
    if(!q)return portfolio;
    return portfolio.filter(({unit})=>{
      const name=(unit.municipalityName||'').toLowerCase();
      const code=String(unit.municipalityCode||'');
      const uf=(unit.uf||'').toLowerCase();
      const id=String(unit.id||'').toLowerCase();
      return name.includes(q)||code.includes(q)||uf.includes(q)||id.includes(q);
    });
  },[portfolio,portfolioQuery]);
  const placesForRadius=useMemo(()=>{
    const byCode=new Map<string,MunicipalityPlace>();
    for(const place of munCenters){
      const code=normalizeMunicipalityCode(place.code);
      if(!code)continue;
      byCode.set(code,{code,name:place.name,latitude:place.latitude,longitude:place.longitude});
    }
    // Coordenadas do cenário (banco/unidades) têm prioridade sobre a malha.
    for(const unit of data?.units||[]){
      if(unit.type==='DISTRITO')continue;
      const code=normalizeMunicipalityCode(unit.municipalityCode);
      if(!code||!Number.isFinite(unit.latitude)||!Number.isFinite(unit.longitude))continue;
      byCode.set(code,{
        code,
        name:unit.municipalityName||byCode.get(code)?.name||code,
        latitude:unit.latitude,
        longitude:unit.longitude,
      });
    }
    return [...byCode.values()];
  },[munCenters,data]);
  const placesInRadius=useMemo(()=>{
    if(!selected||radiusKm<=0)return [] as MunicipalityPlace[];
    return contiguousWithinRadius(selected.latitude,selected.longitude,radiusKm,placesForRadius);
  },[selected,radiusKm,placesForRadius]);
  const selectedFromPortfolio=portfolio.filter(({unit})=>selectedUnits.has(unit.id)).length;
  const municipalityPopulations=useMemo(()=>portfolio
    .filter(({unit})=>unit.type!=='DISTRITO')
    .map(({unit})=>{
      const code=normalizeMunicipalityCode(unit.municipalityCode);
      return population?.values?.[code]??unit.population??0;
    }),[portfolio,population]);
  const nextUnitSelection=(old:Set<string>,id:string,additive=false)=>{
    const keepExisting=!!draft||additive;
    if(!keepExisting&&old.size===1&&old.has(id))return new Set<string>();
    const next=new Set(keepExisting?old:[]);
    next.has(id)?next.delete(id):next.add(id);
    return next;
  };
  const selectFromPortfolio=(id:string)=>{
    const next=nextUnitSelection(selectedUnits,id,false);
    setSelectedUnits(next);
    setWaveUnitId(next.has(id)?id:null);
  };
  const selectFromMap=(id:string,additive=false)=>{
    setSelectedUnits(nextUnitSelection(selectedUnits,id,additive));
    setWaveUnitId(null);
  };
  const stageMeshMunicipality=(payload:{
    unitId?:string;
    municipalityCode:string;
    name:string;
    latitude:number;
    longitude:number;
    population:number;
  })=>{
    if(!selectedPole||!draft||!data)return;
    const code=normalizeMunicipalityCode(payload.municipalityCode);
    if(!code)return;
    let unit=payload.unitId?data.units.find(item=>item.id===payload.unitId):undefined;
    if(!unit){
      const matches=data.units.filter(item=>item.type!=='DISTRITO'&&normalizeMunicipalityCode(item.municipalityCode)===code);
      unit=uniqueUnitsByMunicipality(matches,selectedPole)[0];
    }
    if(!unit){
      const createdId=`MUN-${code}`;
      mutate(d=>{
        if(d.units.some(item=>item.type!=='DISTRITO'&&normalizeMunicipalityCode(item.municipalityCode)===code))return d;
        d.units.push({
          id:createdId,
          type:'MUNICIPIO',
          municipalityCode:code,
          municipalityName:payload.name,
          latitude:payload.latitude,
          longitude:payload.longitude,
          population:payload.population||0,
          stores:0,
          poleId:null,
          distanceKm:0,
        });
        return d;
      });
      setSelectedUnits(old=>nextUnitSelection(old,createdId,true));
      setWaveUnitId(null);
      return;
    }
    setSelectedUnits(old=>nextUnitSelection(old,unit!.id,true));
    setWaveUnitId(null);
  };
  const stageMeshDistrict=(payload:{
    unitId?:string;
    districtCode:string;
    municipalityCode:string;
    name:string;
    municipalityName?:string;
    latitude:number;
    longitude:number;
    population:number;
  })=>{
    if(!selectedPole||!draft||!data)return;
    const districtCode=String(payload.districtCode||'').replace(/\D/g,'');
    if(!districtCode)return;
    const munCode=normalizeMunicipalityCode(payload.municipalityCode);
    let unit=payload.unitId?data.units.find(item=>item.id===payload.unitId):undefined;
    if(!unit){
      unit=data.units.find(item=>item.type==='DISTRITO'&&String(item.districtCode||'').replace(/\D/g,'')===districtCode);
    }
    if(!unit){
      const createdId=`DIST-${districtCode}`;
      mutate(d=>{
        if(d.units.some(item=>item.type==='DISTRITO'&&String(item.districtCode||'').replace(/\D/g,'')===districtCode))return d;
        d.units.push({
          id:createdId,
          type:'DISTRITO',
          municipalityCode:munCode||'',
          districtCode,
          municipalityName:payload.name||payload.municipalityName||districtCode,
          latitude:payload.latitude,
          longitude:payload.longitude,
          population:payload.population||0,
          stores:0,
          poleId:null,
          distanceKm:0,
        });
        return d;
      });
      setSelectedUnits(old=>nextUnitSelection(old,createdId,true));
      setWaveUnitId(null);
      return;
    }
    setSelectedUnits(old=>nextUnitSelection(old,unit!.id,true));
    setWaveUnitId(null);
  };
  const clearSelection=()=>{setSelectedUnits(new Set());setWaveUnitId(null);};
  const selectWithinRadius=()=>{
    if(!selected||!data||!draft||!placesInRadius.length)return;
    const ids=new Set<string>();
    mutate(d=>{
      for(const place of placesInRadius){
        const matches=d.units.filter(unit=>unit.type!=='DISTRITO'&&normalizeMunicipalityCode(unit.municipalityCode)===place.code);
        let unit=uniqueUnitsByMunicipality(matches,selected.id)[0];
        if(!unit){
          unit={
            id:`MUN-${place.code}`,
            type:'MUNICIPIO',
            municipalityCode:place.code,
            municipalityName:place.name||place.code,
            latitude:place.latitude,
            longitude:place.longitude,
            population:population?.values?.[place.code]||0,
            stores:0,
            poleId:null,
            distanceKm:0,
          };
          d.units.push(unit);
        }
        ids.add(unit.id);
      }
      // Distritos já existentes dentro do raio também entram na seleção.
      for(const unit of d.units){
        if(unit.type!=='DISTRITO')continue;
        if(haversineKm(selected.latitude,selected.longitude,unit.latitude,unit.longitude)<=radiusKm)ids.add(unit.id);
      }
      return d;
    });
    setSelectedUnits(ids);
    setWaveUnitId(null);
  };
  const clearRadius=()=>{setRadiusKm(0);};
  const pushHistory=()=>{if(!data)return;setPast(x=>[...x.slice(-39),{poles:structuredClone(data.poles),units:structuredClone(data.units)}]);setFuture([]);};
  const syncSummary=(d:ScenarioData):ScenarioData=>({...d,summary:{...d.summary,poleCount:d.poles.length,areaCounts:countPolesByArea(d.poles)}});
  const applyScenario=(next:ScenarioData)=>{
    const synced=syncSummary(next);
    setData(synced);
    setDraft(current=>current?{...current,data:synced}:current);
    return synced;
  };
  const mutate=(fn:(d:ScenarioData)=>ScenarioData)=>{if(!data||!draft)return;pushHistory();applyScenario(fn(structuredClone(data)));};
  const movePole=(id:string,longitude:number,latitude:number,host?:PoleHostMunicipality)=>mutate(d=>{
    const pole=d.poles.find(item=>item.id===id);
    if(!pole)return d;
    const hinted=normalizeMunicipalityCode(host?.code);
    const place=hinted&&hinted!=='0000000'
      ?placesForRadius.find(item=>item.code===hinted)||{code:hinted,name:host?.name,latitude,longitude}
      :nearestMunicipalityPlace(latitude,longitude,placesForRadius);
    const unit=place?.code
      ?d.units.find(item=>item.type!=='DISTRITO'&&normalizeMunicipalityCode(item.municipalityCode)===place.code)
      :undefined;
    relocatePoleSeat(pole,longitude,latitude,place?{
      code:place.code,
      name:host?.name||place.name||unit?.municipalityName,
      uf:host?.uf||unit?.uf||ufFromMunicipalityCode(place.code),
    }:undefined);
    d.units.filter(unit=>unit.poleId===id).forEach(unit=>{
      unit.distanceKm=haversineKm(latitude,longitude,unit.latitude,unit.longitude);
    });
    return d;
  });
  const changePoleArea=(poleId:string,rawArea:string)=>{
    const known=[...new Set([...(data?.poles||[]).map(pole=>pole.area),...(currentData?.poles||[]).map(pole=>pole.area)])];
    const area=resolveAreaName(rawArea,known);
    const pole=data?.poles.find(item=>item.id===poleId);
    if(!area||!pole||pole.area===area)return;
    mutate(d=>{const target=d.poles.find(item=>item.id===poleId);if(target)target.area=area;return d;});
    if(selectedArea===pole.area)setSelectedArea(area);
  };
  const assign=()=>{
    if(!selectedPole||!selectedUnits.size)return;
    const ids=new Set(selectedUnits);
    mutate(d=>{
      const pole=d.poles.find(x=>x.id===selectedPole);
      if(!pole)return d;
      d.units.filter(u=>ids.has(u.id)).forEach(u=>{
        u.poleId=selectedPole;
        u.distanceKm=haversineKm(pole.latitude,pole.longitude,u.latitude,u.longitude);
      });
      // Evita o mesmo município aparecer 2x na carteira após atribuir
      d.units=mergeDuplicateMunicipalities(d.units,selectedPole);
      d.territories={
        ...d.territories,
        features:d.territories.features.map((feature:any)=>{
          const props=feature.properties||{};
          const unitId=String(props.DEMAND_ID||props._unitId||'');
          const districtCode=String(props.CD_DIST||'').replace(/\D/g,'');
          const code=normalizeMunicipalityCode(props.CD_MUN||props.COD_IBGE||props.id||'');
          const unit=d.units.find(item=>item.id===unitId)
            ||(districtCode?d.units.find(item=>item.type==='DISTRITO'&&String(item.districtCode||'').replace(/\D/g,'')===districtCode&&item.poleId===selectedPole):undefined)
            ||d.units.find(item=>item.type!=='DISTRITO'&&item.poleId===selectedPole&&normalizeMunicipalityCode(item.municipalityCode)===code);
          if(!unit||unit.poleId!==selectedPole)return feature;
          return {...feature,properties:{
            ...props,
            DEMAND_ID:unit.id,
            GERENCIA_ID:unit.poleId,
            DISTANCIA_KM:unit.distanceKm,
            TIPO_UNIDADE:unit.type,
            CD_DIST:unit.districtCode||props.CD_DIST,
            CD_MUN:normalizeMunicipalityCode(unit.municipalityCode)||code,
            POPULACAO_UNIDADE:unit.population,
            QTD_LOJAS:unit.stores,
          }};
        }),
      };
      return d;
    });
    clearSelection();
  };
  const removeFromPortfolio=()=>{if(!selectedPole||!selectedFromPortfolio)return;mutate(d=>{d.units.filter(u=>u.poleId===selectedPole&&selectedUnits.has(u.id)).forEach(u=>{u.poleId=null;u.distanceKm=0;});return d;});clearSelection();};
  const redistribute=()=>{if(!selected||!selectedUnits.size)return;mutate(d=>{const candidates=d.poles.filter(p=>p.area===selected.area);d.units.filter(u=>selectedUnits.has(u.id)).forEach(u=>{const p=candidates.reduce((best,p)=>haversineKm(u.latitude,u.longitude,p.latitude,p.longitude)<haversineKm(u.latitude,u.longitude,best.latitude,best.longitude)?p:best,candidates[0]);u.poleId=p.id;u.distanceKm=haversineKm(u.latitude,u.longitude,p.latitude,p.longitude);});return d;});clearSelection();};
  const undo=()=>{if(!data||!past.length)return;const prev=past.at(-1)!;setFuture(x=>[{poles:data.poles,units:data.units},...x]);applyScenario({...data,poles:prev.poles,units:prev.units});setPast(x=>x.slice(0,-1));};
  const redo=()=>{if(!data||!future.length)return;const next=future[0];setPast(x=>[...x,{poles:data.poles,units:data.units}]);applyScenario({...data,poles:next.poles,units:next.units});setFuture(x=>x.slice(1));};
  const createBuilder=async()=>{if(!data)return;const name=`Builder — ${data.summary.name}`;const d=await api.createDraft(name,data.summary.id,{...structuredClone(data),summary:{...data.summary,id:'draft',name,kind:'draft'}});await refreshLists();setActive(d.id);};
  const downloadBlob=(blob:Blob,filename:string)=>{
    const url=URL.createObjectURL(blob);
    const link=document.createElement('a');
    link.href=url;
    link.download=filename;
    link.click();
    URL.revokeObjectURL(url);
  };
  const downloadUtf8Json=(value:unknown,filename:string)=>{
    const json='\uFEFF'+JSON.stringify(value,null,2);
    downloadBlob(new Blob([new TextEncoder().encode(json)],{type:'application/json;charset=utf-8'}),filename);
  };
  const persistDraft=async()=>{
    if(!draft||!data)throw new Error('Nenhum rascunho ativo.');
    const saved=await api.saveDraft({...draft,data:syncSummary(data)});
    setDraft(saved);
    setData(saved.data);
    return saved;
  };
  const save=async()=>{
    try{
      await persistDraft();
      setError('Rascunho salvo.');
      window.setTimeout(()=>setError(current=>current==='Rascunho salvo.'?'':current),2200);
    }catch(e:any){setError(e.message);}
  };
  const exportJson=async()=>{
    try{
      const saved=await persistDraft();
      downloadUtf8Json(saved,`${saved.name.replace(/[^\w\-]+/g,'_')||saved.id}.json`);
      setError('JSON exportado com o rascunho atual.');
      window.setTimeout(()=>setError(current=>current==='JSON exportado com o rascunho atual.'?'':current),2200);
    }catch(e:any){setError(e.message);}
  };
  const exportGeojson=async()=>{
    try{
      const saved=await persistDraft();
      const response=await fetch(`/api/drafts/${saved.id}/geojson`);
      if(!response.ok)throw new Error((await response.json().catch(()=>({message:response.statusText}))).message);
      downloadBlob(new Blob([await response.arrayBuffer()],{type:'application/geo+json;charset=utf-8'}),`${saved.name.replace(/[^\w\-]+/g,'_')||saved.id}.geojson`);
      setError('GeoJSON exportado com o rascunho atual.');
      window.setTimeout(()=>setError(current=>current==='GeoJSON exportado com o rascunho atual.'?'':current),2200);
    }catch(e:any){setError(e.message);}
  };
  const refreshCurrent=async()=>{try{await api.refresh();setError('Atualizando lojas ativas…');const poll=window.setInterval(async()=>{const status=await api.cacheStatus();if(status.refreshing)return;window.clearInterval(poll);if(status.lastError){setError(status.lastError);return;}await refreshLists();setActive('current');setError('');},2000);}catch(e:any){setError(e.message);}};
  const importDraft=async(file?:File)=>{
    if(!file)return;
    try{
      const parsed=JSON.parse((await file.text()).replace(/^\uFEFF/,'')) as any;
      const sourceData=parsed?.data?.units&&parsed?.data?.poles?parsed.data:parsed?.units&&parsed?.poles?parsed:null;
      if(!sourceData)throw new Error('JSON sem poles/units. Exporte pelo botão JSON do Builder.');
      const name=String(file.name.replace(/\.json$/i,'')||'Rascunho importado');
      const baseScenarioId=String(parsed?.baseScenarioId||parsed?.data?.summary?.id||'current');
      const created=await api.createDraft(name,baseScenarioId,structuredClone(sourceData));
      await refreshLists();
      setActive(created.id);
      setError('');
    }catch(e:any){setError(`Arquivo inválido: ${e.message}`);}
    finally{if(importInput.current)importInput.current.value='';}
  };
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
  const toggleExcluded=async()=>{
    if(showExcluded){setShowExcluded(false);return;}
    setShowExcluded(true);
    if(excludedMunicipalities)return;
    try{setExcludedMunicipalities(await api.excludedMunicipalities());setError('');}catch(e:any){setShowExcluded(false);setError(`Municípios excluídos: ${e.message}`);}
  };
  const comparisonAvailable=!!currentData&&active!=='current';
  const movements=useMemo(()=>comparisonAvailable&&data?matchPoleMovements(currentData.poles,data.poles):[],[comparisonAvailable,currentData,data]);
  const proposedAreaCounts=useMemo(()=>countPolesByArea(data?.poles||[]),[data]);
  const currentAreaCounts=useMemo(()=>countPolesByArea(currentData?.poles||[]),[currentData]);
  const areaImpacts=useMemo(()=>compareAreaCounts(comparisonAvailable?currentAreaCounts:{},proposedAreaCounts),[comparisonAvailable,currentAreaCounts,proposedAreaCounts]);
  const areas=useMemo(()=>areaImpacts.filter(item=>item.area.toLowerCase().includes(filter.toLowerCase())),[areaImpacts,filter]);
  const areaNames=useMemo(()=>areaImpacts.map(item=>item.area),[areaImpacts]);
  const totalImpact=(data?.poles.length||0)-(comparisonAvailable?currentData.poles.length:0);
  const areaPoleCards=useMemo(()=>{
    if(!data||!showAll||selected)return [];
    return data.poles
      .filter(pole=>!selectedArea||pole.area===selectedArea)
      .map(pole=>({pole,metrics:calculatePoleMetrics(pole,data.units)}))
      .sort((a,b)=>{
        const byArea=a.pole.area.localeCompare(b.pole.area,'pt-BR');
        return byArea||a.pole.name.localeCompare(b.pole.name,'pt-BR');
      });
  },[data,selectedArea,showAll,selected]);
  const options=[...scenarios.map(s=>({id:s.id,label:`${s.kind==='current'?'Atual':s.kind.toUpperCase()} · ${s.name}`})),...draftRefs.map(d=>({id:d.id,label:`Builder · ${d.name}`}))];
  return <main className="shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">T</span><div><strong>Territórios BE</strong><small>Planejamento de cobertura</small></div></div>
      <select value={active} onChange={e=>setActive(e.target.value)} aria-label="Cenário">{options.map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select>
      <div className="top-actions">
        <div className="icon-cluster">
          <IconBtn className={showPopulation?'heat-active':''} pressed={showPopulation} label={showPopulation?'Ocultar população':'Mapa de calor'} onClick={togglePopulation}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18h16M6 18l3-8 3 5 2-3 4 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round"/></svg>
          </IconBtn>
          {comparisonAvailable&&<>
            <IconBtn className={showCurrentPoles?'comparison-active':''} pressed={showCurrentPoles} label={showCurrentPoles?'Ocultar visão atual':'Comparar com atual'} onClick={()=>setShowCurrentPoles(value=>!value)}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="12" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="15" cy="12" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.8"/></svg>
            </IconBtn>
            {showCurrentPoles&&<IconBtn className={showMovementLines?'movement-active':''} pressed={showMovementLines} label={showMovementLines?'Ocultar movimentos':'Mostrar movimentos'} onClick={()=>setShowMovementLines(value=>!value)}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 16h8M13 16l-2.5-2.5M13 16l-2.5 2.5M19 8H11M11 8l2.5-2.5M11 8l2.5 2.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </IconBtn>}
          </>}
          <IconBtn className={!showPoles?'poles-hidden':''} pressed={showPoles} label={showPoles?'Ocultar polos comerciais':'Mostrar polos comerciais'} onClick={()=>setShowPoles(value=>!value)}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-6-5.4-6-10a6 6 0 1 1 12 0c0 4.6-6 10-6 10z" fill="none" stroke="currentColor" strokeWidth="1.8"/><circle cx="12" cy="11" r="2" fill="currentColor"/></svg>
          </IconBtn>
          <IconBtn className={showExcluded?'excluded-active':''} pressed={showExcluded} label={showExcluded?`Ocultar excluídos (${excludedMunicipalities?.count||0})`:'Municípios excluídos'} onClick={toggleExcluded}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="M7 7l10 10" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>
          </IconBtn>
          <IconBtn className={showRegionals?'regional-active':''} pressed={showRegionals} label={showRegionals?`Ocultar regionais (${regionals?.count||0})`:'Gerências regionais'} onClick={toggleRegionals}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V9l8-5 8 5v11" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M9 20v-6h6v6" fill="none" stroke="currentColor" strokeWidth="1.8"/></svg>
          </IconBtn>
          <IconBtn className={showAll?'primary':''} pressed={showAll} label={showAll?'Ocultar carteiras':'Mostrar todas as áreas'} onClick={()=>setShowAll(x=>!x)}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>
          </IconBtn>
        </div>
        <button onClick={refreshCurrent}>Atualizar lojas</button>
        <button onClick={()=>importInput.current?.click()}>Importar</button>
        <input ref={importInput} hidden type="file" accept="application/json,.json" onChange={e=>importDraft(e.target.files?.[0])}/>
        {!draft&&data&&<button className="accent" onClick={createBuilder}>Abrir no Builder</button>}
        {draft&&<>
          <IconBtn label="Desfazer" disabled={!past.length} onClick={undo}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 8H5V4M5 8a8 8 0 1 1-1.5 6.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </IconBtn>
          <IconBtn label="Refazer" disabled={!future.length} onClick={redo}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 8h4V4M19 8a8 8 0 1 0 1.5 6.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </IconBtn>
          <button className="button-link" onClick={exportJson}>JSON</button>
          <button className="button-link" onClick={exportGeojson}>GeoJSON</button>
          <button className="accent" onClick={save}>Salvar rascunho</button>
        </>}
      </div>
    </header>
    <section className="workspace"><aside className="legend-panel"><div className="panel-title"><div><small>AGRUPAMENTO</small><h2>Gerências de área</h2></div><b>{selectedArea?data?.poles.filter(pole=>pole.area===selectedArea).length:data?.poles.length||0}</b></div>{comparisonAvailable&&<div className="area-comparison-summary"><span>Atual <b>{currentData.poles.length}</b></span><span>→</span><span>Cenário <b>{data?.poles.length||0}</b></span><em className={totalImpact>0?'increase':totalImpact<0?'decrease':'stable'}>{totalImpact>0?'+':''}{totalImpact}</em></div>}<input placeholder="Filtrar área…" value={filter} onChange={e=>setFilter(e.target.value)}/><div className="area-list">{areas.map(item=><button key={item.area} className={selectedArea===item.area?'active':''} onClick={()=>{setSelectedArea(current=>current===item.area?null:item.area);setSelectedPole(null);clearSelection();setRadiusKm(0);setPortfolioQuery('');}}><i style={{background:areaColor(item.area,areaNames)}}/><span className="area-label">{item.area}</span>{comparisonAvailable?<span className="area-impact"><b>{item.proposed}</b><small className={item.delta>0?'increase':item.delta<0?'decrease':'stable'}>{item.delta>0?'+':''}{item.delta} vs. atual</small></span>:<b>{item.proposed}</b>}</button>)}</div>{selectedArea&&<button className="clear-area" onClick={()=>setSelectedArea(null)}>Mostrar todas as gerências</button>}<div className="hint"><span>●</span><div><b>{draft?'Modo edição ativo':'Modo exploração'}</b><small>{draft?'Troque a gerência de área, arraste polos ou selecione territórios.':'Clique em um polo para ver sua carteira.'}</small></div></div></aside>
      <div className="map-wrap">{config.mapboxToken?<MapView data={data} token={config.mapboxToken} styleUrl={config.mapboxStyle} selectedPole={selectedPole} selectedUnits={selectedUnits} waveUnitId={waveUnitId} selectedArea={selectedArea} showAll={showAll} showPoles={showPoles} comparisonPoles={currentData?.poles||[]} movements={movements} showComparisonPoles={showCurrentPoles} showMovementLines={showMovementLines} showPopulation={showPopulation} population={population?.values||{}} showExcluded={showExcluded} excludedCodes={excludedMunicipalities?.codes||[]} showRegionals={showRegionals} regionals={regionals?.points||[]} editable={!!draft} radiusKm={radiusKm} onPole={id=>{setSelectedPole(id);setWaveUnitId(null);setRadiusKm(0);setPortfolioQuery('');}} onUnit={selectFromMap} onStageMeshMunicipality={stageMeshMunicipality} onStageMeshDistrict={stageMeshDistrict} onMovePole={movePole} onBoxSelect={ids=>{
              const picked=(data?.units||[]).filter(unit=>ids.includes(unit.id));
              const unique=uniqueUnitsByMunicipality(picked,selectedPole);
              setSelectedUnits(new Set(unique.map(unit=>unit.id)));
              setWaveUnitId(null);
            }}/>:<div className="empty"><h2>Token Mapbox ausente</h2><p>Adicione MAPBOX_ACCESS_TOKEN ao arquivo .env para carregar o mapa.</p></div>}{showCurrentPoles&&comparisonAvailable&&<div className="comparison-legend"><div><i/><span>Polos atuais</span></div><div><i className="proposed-swatch"/><span>Polos do cenário</span></div>{showMovementLines&&<div><i className="movement-swatch"/><span>Movimento estimado</span></div>}<small>Pareamento pela menor distância, priorizando a mesma gerência de área.</small></div>}{showPopulation&&<div className="population-legend"><h3>População</h3><small>Municípios e distritos (≥300 mil){population?.censusYear?` · Censo ${population.censusYear}`:''}</small>{POPULATION_BANDS.map(band=><div key={band.label}><i style={{background:band.color}}/><span>{band.label}</span></div>)}{population?.stale&&<em>Exibindo o último dado disponível</em>}</div>}{showExcluded&&<div className="excluded-legend"><h3>Municípios excluídos</h3><small>{excludedMunicipalities?.count||0} códigos · {excludedMunicipalities?.source||'SQL'}</small><div><i/><span>Marrom escuro na malha</span></div>{excludedMunicipalities?.stale&&<em>Exibindo o último dado disponível</em>}</div>}{showRegionals&&regionals?.stale&&<div className="regional-cache-note">Regionais: último dado disponível</div>}{loading&&<div className="loading">Carregando cenário…</div>}{error&&<div className="toast" onClick={()=>setError('')}>{error}</div>}</div>
      <aside className="detail-panel">{selected&&metrics?<>
        {showAll&&<button className="back-area" onClick={()=>{setSelectedPole(null);clearSelection();setRadiusKm(0);setPortfolioQuery('');}}>← Voltar aos polos</button>}
        <div className="selection-head"><span style={{background:territoryColor(selected,data?.poles||[])}}/><div><small>POLO SELECIONADO</small><h2>{selected.name}</h2><p>{selected.area}{selected.uf?` · ${selected.uf}`:''}</p></div></div>
        {draft&&<PoleAreaTransfer currentArea={selected.area} areas={areaNames} onChange={area=>changePoleArea(selected.id,area)}/>}
        <div className="metrics">
          <Metric label="Municípios" value={fmt.format(metrics.municipalities)} hint="Clique para ver faixas de população" active={showMunicipalitySizes} onClick={()=>setShowMunicipalitySizes(open=>!open)}/>
          <Metric label="Lojas" value={fmt.format(metrics.stores)}/>
          <Metric label="População" value={fmt.format(metrics.population)}/>
          {metrics.districts>0
            ?<Metric label="Distritos" value={fmt.format(metrics.districts)}/>
            :<Metric label="Raio médio" value={`${km.format(metrics.meanKm)} km`}/>}
        </div>
        {showMunicipalitySizes&&<MunicipalitySizeBreakdown populations={municipalityPopulations}/>}
        <h3>Distâncias da carteira</h3><div className="radius"><div><small>MÍNIMO</small><b>{km.format(metrics.minKm)} km</b></div><div><small>MÉDIO</small><b>{km.format(metrics.meanKm)} km</b></div><div><small>MÁXIMO</small><b>{km.format(metrics.maxKm)} km</b></div></div>
        {draft&&<div className="builder-actions">
          <div className="radius-tool">
            <label className="radius-tool-head" htmlFor="radius-slider"><span>Raio contíguo</span><b>{km.format(radiusKm)} km{radiusKm>0?` · ${fmt.format(placesInRadius.length)}`:''}</b></label>
            <input id="radius-slider" type="range" min={0} max={300} step={0.5} value={radiusKm} onChange={e=>setRadiusKm(Number(e.target.value))} aria-label="Raio de seleção em km"/>
            <div className="builder-row">
              <button disabled={!placesInRadius.length} onClick={selectWithinRadius}>Selecionar raio</button>
              <button disabled={radiusKm<=0} className="ghost" onClick={clearRadius}>Limpar raio</button>
            </div>
          </div>
          <div className="builder-row builder-row-main">
            <button disabled={!selectedUnits.size} className="accent" onClick={assign}>Atribuir{selectedUnits.size?` (${selectedUnits.size})`:''}</button>
            <button disabled={!selectedFromPortfolio} className="danger" onClick={removeFromPortfolio}>Retirar{selectedFromPortfolio?` (${selectedFromPortfolio})`:''}</button>
          </div>
          <div className="builder-row">
            <button disabled={!selectedUnits.size} onClick={redistribute}>Redistribuir</button>
            <button disabled={!selectedUnits.size} className="ghost" onClick={clearSelection}>Limpar</button>
          </div>
        </div>}
        <div className="portfolio-list">
          <div className="portfolio-title"><h3>Carteira</h3><small>{portfolioQuery?`${filteredPortfolio.length}/${portfolio.length}`:'Maior distância primeiro'}</small></div>
          <input className="portfolio-search" placeholder="Buscar município, IBGE ou UF…" value={portfolioQuery} onChange={e=>setPortfolioQuery(e.target.value)} aria-label="Buscar na carteira"/>
          {filteredPortfolio.slice(0,100).map(({unit,distance})=>{const share=metrics.population?((unit.population||0)/metrics.population)*100:0;return <button key={unit.id} aria-pressed={selectedUnits.has(unit.id)} className={selectedUnits.has(unit.id)?'selected':''} onClick={()=>selectFromPortfolio(unit.id)}><i className="portfolio-check">{selectedUnits.has(unit.id)?'✓':''}</i><span>{unit.municipalityName||unit.id}<small>{unit.type==='DISTRITO'?'Distrito':'Município'} · {unit.stores} lojas</small></span><em className="portfolio-stats"><b>{km.format(distance)} km</b><small>{fmt.format(unit.population||0)} · {pct.format(share)}%</small></em></button>;})}
          {portfolio.length>0&&!filteredPortfolio.length&&<p className="portfolio-empty">Nenhum município encontrado.</p>}
        </div>
      </>:areaPoleCards.length?<>
        <div className="selection-head"><span style={{background:selectedArea?areaColor(selectedArea,areaNames):'#39d98a'}}/><div><small>{selectedArea?'GERÊNCIA DE ÁREA':'ÂMBITO GERAL'}</small><h2>{selectedArea||'Todos os polos'}</h2><p>{areaPoleCards.length} polos · carteiras no mapa</p></div></div>
        <div className="area-poles">{areaPoleCards.map(({pole,metrics:m})=><button key={pole.id} className="pole-card" onClick={()=>{setSelectedPole(pole.id);clearSelection();setRadiusKm(0);setPortfolioQuery('');}}>
          <div className="pole-card-head"><i style={{background:territoryColor(pole,data?.poles||[])}}/><div><strong>{pole.name}</strong><small>{!selectedArea?`${pole.area} · `:''}{pole.uf?`${pole.uf} · `:''}{fmt.format(m.municipalities)} mun. · {fmt.format(m.stores)} lojas</small></div></div>
          <div className="pole-card-radius"><span><small>Mín</small><b>{km.format(m.minKm)}</b></span><span><small>Méd</small><b>{km.format(m.meanKm)}</b></span><span><small>Máx</small><b>{km.format(m.maxKm)}</b></span></div>
        </button>)}</div>
      </>:<div className="empty side"><h2>Selecione um polo</h2><p>As métricas e a carteira aparecerão aqui.</p></div>}</aside>
    </section>
  </main>;
}
function Metric({label,value,hint,active,onClick}:{label:string;value:string;hint?:string;active?:boolean;onClick?:()=>void}){
  const body=<><small>{label}</small><b>{value}</b></>;
  if(!onClick)return <div>{body}</div>;
  return <button type="button" className={active?'active':''} title={hint||label} aria-label={hint||label} aria-expanded={!!active} onClick={onClick}>{body}</button>;
}
function IconBtn({label,pressed,disabled,className='',onClick,children}:{label:string;pressed?:boolean;disabled?:boolean;className?:string;onClick:()=>void;children:React.ReactNode}){
  return <button type="button" className={`icon-btn${className?` ${className}`:''}`} title={label} aria-label={label} aria-pressed={pressed} disabled={disabled} onClick={onClick}>{children}</button>;
}
