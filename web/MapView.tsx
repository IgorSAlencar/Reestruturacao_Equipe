import { useEffect, useMemo, useRef, useState } from 'react';
import mapboxgl, { type GeoJSONSource, type Map as MapboxMap, type MapMouseEvent } from 'mapbox-gl';
import type { FeatureCollection, Geometry } from 'geojson';
import type { RegionalOffice, ScenarioData } from '../shared/types';
import { populationBandIndex, POPULATION_BANDS } from '../shared/population';
import { poleColor } from '../shared/mapColors';

type Props={
  data:ScenarioData|null;
  token:string;
  styleUrl:string;
  selectedPole:string|null;
  selectedUnits:Set<string>;
  selectedArea:string|null;
  showAll:boolean;
  showPopulation:boolean;
  population:Record<string,number>;
  showRegionals:boolean;
  regionals:RegionalOffice[];
  editable:boolean;
  onPole:(id:string)=>void;
  onUnit:(id:string,additive:boolean)=>void;
  onMovePole:(id:string,lng:number,lat:number)=>void;
  onBoxSelect:(ids:string[])=>void;
};

const codeExpression:any=['to-string',['coalesce',['get','CD_MUN'],['get','id'],['get','COD_IBGE'],'']];

export default function MapView(p:Props){
  const container=useRef<HTMLDivElement>(null);
  const map=useRef<MapboxMap|null>(null);
  const markers=useRef<mapboxgl.Marker[]>([]);
  const regionalMarkers=useRef<mapboxgl.Marker[]>([]);
  const popup=useRef<mapboxgl.Popup|null>(null);
  const dataRef=useRef(p.data);
  const callbacks=useRef(p);
  const [styleReady,setStyleReady]=useState(false);
  const poles=useMemo(()=>p.data?.poles||[],[p.data]);
  const colorForPole=(pole:ScenarioData['poles'][number])=>poleColor(pole,poles);
  dataRef.current=p.data;
  callbacks.current=p;

  useEffect(()=>{
    if(!container.current||map.current||!p.token)return;
    mapboxgl.accessToken=p.token;
    const m=new mapboxgl.Map({
      container:container.current,
      style:p.styleUrl,
      projection:'mercator',
      center:[-52.5,-14.8],
      zoom:3,
      minZoom:2.6,
      maxBounds:[[-76,-36],[-32,7]],
      attributionControl:false,
    });
    map.current=m;
    m.boxZoom.disable();
    m.addControl(new mapboxgl.NavigationControl({showCompass:false}),'bottom-right');
    m.on('load',()=>{
      m.setProjection('mercator');
      m.addSource('brazil-mask',{type:'geojson',data:'/brazil-mask.json'});
      m.addLayer({id:'world-mask',type:'fill',source:'brazil-mask',filter:['==',['get','kind'],'mask'],paint:{'fill-color':'#07101d','fill-opacity':.88}});
      m.addSource('municipalities',{type:'geojson',data:'/api/geometry/municipalities'});
      m.addLayer({id:'municipality-fill',type:'fill',source:'municipalities',paint:{'fill-color':'#152338','fill-opacity':.1}});
      m.addLayer({id:'portfolio-fill',type:'fill',source:'municipalities',paint:{'fill-color':'#38bdf8','fill-opacity':0}});
      m.addLayer({id:'municipality-line',type:'line',source:'municipalities',paint:{'line-color':'#8391a7','line-width':['interpolate',['linear'],['zoom'],3,.15,8,.8],'line-opacity':.55}});
      m.addLayer({id:'brazil-outline',type:'line',source:'brazil-mask',filter:['==',['get','kind'],'outline'],paint:{'line-color':'#a6b4c7','line-width':['interpolate',['linear'],['zoom'],2.6,.8,7,1.8],'line-opacity':.85}});
      m.addSource('territories',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      m.addLayer({id:'territory-fill',type:'fill',source:'territories',paint:{'fill-color':['coalesce',['get','_color'],'#58a6ff'],'fill-opacity':['case',['boolean',['get','_visible'],false],.48,.02]}});
      m.addLayer({id:'territory-line',type:'line',source:'territories',paint:{'line-color':['case',['boolean',['get','_selected'],false],'#ffffff',['coalesce',['get','_color'],'#58a6ff']],'line-width':['case',['boolean',['get','_selected'],false],2.8,.7],'line-opacity':.9}});
      const showMunicipality=(e:mapboxgl.MapLayerMouseEvent)=>{
        const feature=e.features?.[0];
        if(!feature)return;
        const directId=String(feature.properties?._unitId||'');
        const direct=dataRef.current?.units.find(unit=>unit.id===directId);
        const code=String(feature.properties?.id||feature.properties?.CD_MUN||feature.properties?.COD_IBGE||direct?.municipalityCode||'').padStart(7,'0');
        const units=dataRef.current?.units.filter(unit=>unit.municipalityCode===code)||[];
        const unit=direct||units.find(item=>item.poleId===callbacks.current.selectedPole)||units[0];
        const name=String(unit?.municipalityName||feature.properties?.name||feature.properties?.NM_MUN||feature.properties?.description||`Município ${code}`);
        const population=callbacks.current.population[code]??Math.max(0,...units.map(item=>item.population||0));
        const stores=units.reduce((total,item)=>total+(item.stores||0),0);
        const assignedPole=dataRef.current?.poles.find(item=>item.id===unit?.poleId);
        popup.current?.remove();
        popup.current=new mapboxgl.Popup({closeButton:true,closeOnClick:true,offset:12,maxWidth:'300px'})
          .setLngLat(e.lngLat)
          .setDOMContent(municipalityCard({name,code,population,stores,pole:assignedPole?.name||'Sem polo atribuído'}))
          .addTo(m);
        if(unit)callbacks.current.onUnit(unit.id,!!e.originalEvent.ctrlKey||!!e.originalEvent.metaKey);
      };
      m.on('click','territory-fill',showMunicipality);
      m.on('click','municipality-fill',(e)=>{
        if(m.queryRenderedFeatures(e.point,{layers:['territory-fill']}).length)return;
        showMunicipality(e);
      });
      for(const layer of ['territory-fill','municipality-fill']){
        m.on('mouseenter',layer,()=>{m.getCanvas().style.cursor='pointer';});
        m.on('mouseleave',layer,()=>{m.getCanvas().style.cursor='';});
      }
      let start:mapboxgl.Point|null=null,box:HTMLDivElement|null=null;
      m.on('mousedown',(e:MapMouseEvent)=>{if(!e.originalEvent.shiftKey)return;e.preventDefault();start=e.point;m.dragPan.disable();box=document.createElement('div');box.className='selection-box';m.getContainer().appendChild(box);});
      m.on('mousemove',(e:MapMouseEvent)=>{if(!start||!box)return;const minX=Math.min(start.x,e.point.x),minY=Math.min(start.y,e.point.y),maxX=Math.max(start.x,e.point.x),maxY=Math.max(start.y,e.point.y);Object.assign(box.style,{left:`${minX}px`,top:`${minY}px`,width:`${maxX-minX}px`,height:`${maxY-minY}px`});});
      m.on('mouseup',(e:MapMouseEvent)=>{if(!start)return;const features=m.queryRenderedFeatures([start,e.point],{layers:['territory-fill','municipality-fill']});const ids=new Set<string>();features.forEach(f=>{const direct=String(f.properties?._unitId||'');if(direct)ids.add(direct);else{const code=String(f.properties?.id||f.properties?.CD_MUN||'');dataRef.current?.units.filter(u=>u.municipalityCode===code).forEach(u=>ids.add(u.id));}});callbacks.current.onBoxSelect([...ids]);box?.remove();box=null;start=null;m.dragPan.enable();});
      setStyleReady(true);
    });
    return()=>{setStyleReady(false);popup.current?.remove();popup.current=null;m.remove();map.current=null;};
  },[p.token,p.styleUrl]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady||!p.data)return;
    markers.current.forEach(marker=>marker.remove());
    markers.current=[];
    p.data.poles.filter(pole=>!p.selectedArea||pole.area===p.selectedArea).forEach(pole=>{
      const el=document.createElement('button');
      el.className=`pole-marker ${pole.id===p.selectedPole?'active':''}`;
      el.style.setProperty('--pole-color',colorForPole(pole));
      el.title=`${pole.name} · ${pole.area}`;
      el.setAttribute('aria-label',el.title);
      const marker=new mapboxgl.Marker({element:el,draggable:p.editable}).setLngLat([pole.longitude,pole.latitude]).addTo(m);
      el.onclick=(event)=>{event.stopPropagation();p.onPole(pole.id);};
      marker.on('dragend',()=>{const point=marker.getLngLat();if(point.lng>=-74.5&&point.lng<=-34&&point.lat>=-34&&point.lat<=6)p.onMovePole(pole.id,point.lng,point.lat);else marker.setLngLat([pole.longitude,pole.latitude]);});
      markers.current.push(marker);
    });

    const base=p.data.territories.features.length?p.data.territories:municipalTerritories();
    const features=base.features.map((feature:any,index)=>{
      const properties={...(feature.properties||{})};
      const id=String(properties.DEMAND_ID||properties._unitId||p.data!.units[index]?.id||'');
      const unit=p.data!.units.find(x=>x.id===id)||p.data!.units.find(x=>x.municipalityCode===String(properties.id||properties.CD_MUN||properties.COD_IBGE||''));
      const pole=p.data!.poles.find(x=>x.id===unit?.poleId);
      return {...feature,properties:{...properties,_unitId:unit?.id||id,_poleId:unit?.poleId,_color:pole?colorForPole(pole):'#8391a7',_visible:p.showAll||unit?.poleId===p.selectedPole,_selected:unit?p.selectedUnits.has(unit.id):false}};
    });
    (m.getSource('territories') as GeoJSONSource)?.setData({type:'FeatureCollection',features} as any);

    const colors=new Map<string,string>();
    const portfolioCodes:string[]=[];
    const visibleCodes:string[]=[];
    const selectedCodes:string[]=[];
    p.data.units.forEach(unit=>{
      const pole=p.data!.poles.find(x=>x.id===unit.poleId);
      colors.set(unit.municipalityCode,pole?colorForPole(pole):'#8391a7');
      if(unit.poleId===p.selectedPole)portfolioCodes.push(unit.municipalityCode);
      if(p.showAll||unit.poleId===p.selectedPole)visibleCodes.push(unit.municipalityCode);
      if(p.selectedUnits.has(unit.id))selectedCodes.push(unit.municipalityCode);
    });
    const colorPairs:any[]=[...colors].flatMap(([code,color])=>[code,color]);
    const populationPairs:any[]=[];
    POPULATION_BANDS.forEach((band,index)=>{
      const codes=Object.entries(p.population).filter(([,value])=>populationBandIndex(value)===index).map(([code])=>code);
      if(codes.length)populationPairs.push(codes,band.color);
    });
    m.setPaintProperty('municipality-fill','fill-color',p.showPopulation?['match',codeExpression,...populationPairs,'#152338'] as any:'#152338');
    m.setPaintProperty('municipality-fill','fill-opacity',p.showPopulation?['case',['has','id'],.78,.78] as any:.1);
    m.setPaintProperty('portfolio-fill','fill-color',['match',codeExpression,...colorPairs,'#38bdf8'] as any);
    m.setPaintProperty('portfolio-fill','fill-opacity',['case',['in',codeExpression,['literal',portfolioCodes]],.52,['in',codeExpression,['literal',visibleCodes]],p.showPopulation?.18:.4,0] as any);
    m.setPaintProperty('municipality-line','line-color',['case',['in',codeExpression,['literal',selectedCodes]],'#ffffff',['in',codeExpression,['literal',portfolioCodes]],'#38bdf8','#8391a7'] as any);
    m.setPaintProperty('municipality-line','line-width',['case',['in',codeExpression,['literal',selectedCodes]],2.8,['in',codeExpression,['literal',portfolioCodes]],1.7,['interpolate',['linear'],['zoom'],3,.15,8,.8]] as any);
  },[p.data,p.selectedPole,p.selectedUnits,p.selectedArea,p.showAll,p.showPopulation,p.population,p.editable,styleReady,poles]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady)return;
    regionalMarkers.current.forEach(marker=>marker.remove());
    regionalMarkers.current=[];
    if(!p.showRegionals)return;
    p.regionals.forEach(regional=>{
      const element=document.createElement('button');
      element.className='regional-marker';
      element.textContent='GR';
      element.title=`${regional.name} · Gerência regional`;
      element.setAttribute('aria-label',element.title);
      const marker=new mapboxgl.Marker({element,anchor:'center'}).setLngLat([regional.longitude,regional.latitude]).addTo(m);
      element.onclick=(event)=>{
        event.stopPropagation();
        popup.current?.remove();
        popup.current=new mapboxgl.Popup({closeButton:true,closeOnClick:true,offset:18,maxWidth:'320px'})
          .setLngLat([regional.longitude,regional.latitude])
          .setDOMContent(regionalCard(regional))
          .addTo(m);
      };
      regionalMarkers.current.push(marker);
    });
    return()=>{regionalMarkers.current.forEach(marker=>marker.remove());regionalMarkers.current=[];};
  },[p.showRegionals,p.regionals,styleReady]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady||!p.selectedArea||!p.data)return;
    const poles=p.data.poles.filter(pole=>pole.area===p.selectedArea);
    if(!poles.length)return;
    const bounds=poles.reduce((box,pole)=>box.extend([pole.longitude,pole.latitude] as [number,number]),new mapboxgl.LngLatBounds());
    m.fitBounds(bounds,{padding:70,maxZoom:6.5,duration:700});
  },[p.selectedArea,p.data,styleReady]);

  return <div ref={container} className="map"/>;
}

function municipalTerritories():FeatureCollection<Geometry>{
  return {type:'FeatureCollection',features:[]};
}

function municipalityCard({name,code,population,stores,pole}:{name:string;code:string;population:number;stores:number;pole:string}){
  const card=document.createElement('article');
  card.className='municipality-card';
  const eyebrow=document.createElement('small');
  eyebrow.textContent=`MUNICÍPIO · IBGE ${code}`;
  const title=document.createElement('h3');
  title.textContent=name;
  const metrics=document.createElement('div');
  metrics.className='municipality-card-metrics';
  for(const [label,value] of [['População',new Intl.NumberFormat('pt-BR').format(population)],['Lojas',new Intl.NumberFormat('pt-BR').format(stores)]]){
    const metric=document.createElement('span');
    const caption=document.createElement('small');caption.textContent=label;
    const amount=document.createElement('b');amount.textContent=value;
    metric.append(caption,amount);metrics.append(metric);
  }
  const footer=document.createElement('p');
  footer.textContent=`Polo: ${pole}`;
  card.append(eyebrow,title,metrics,footer);
  return card;
}

function regionalCard(regional:RegionalOffice){
  const card=document.createElement('article');
  card.className='municipality-card regional-card';
  const eyebrow=document.createElement('small');
  eyebrow.textContent=`GERÊNCIA REGIONAL · ${regional.id}`;
  const title=document.createElement('h3');
  title.textContent=regional.name;
  const agencies=document.createElement('div');
  agencies.className='regional-card-count';
  const amount=document.createElement('b');amount.textContent=new Intl.NumberFormat('pt-BR').format(regional.agencies);
  const caption=document.createElement('span');caption.textContent='agências vinculadas';
  agencies.append(amount,caption);
  const address=document.createElement('p');
  address.textContent=regional.address||'Endereço não informado';
  card.append(eyebrow,title,agencies,address);
  return card;
}
