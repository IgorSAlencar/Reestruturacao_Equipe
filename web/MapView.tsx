import { useEffect, useRef } from 'react';
import mapboxgl, { type GeoJSONSource, type Map as MapboxMap, type MapMouseEvent } from 'mapbox-gl';
import type { FeatureCollection, Geometry } from 'geojson';
import type { Pole, ScenarioData } from '../shared/types';

const COLORS=['#39d98a','#58a6ff','#ffb454','#c792ea','#ff6b81','#4fd1c5','#f6e05e','#7f9cf5','#ed64a6','#68d391'];
export const areaColor=(area:string)=>COLORS[Math.abs([...area].reduce((n,c)=>n+c.charCodeAt(0),0))%COLORS.length];

type Props={data:ScenarioData|null;token:string;styleUrl:string;selectedPole:string|null;selectedUnits:Set<string>;showAll:boolean;editable:boolean;onPole:(id:string)=>void;onUnit:(id:string,additive:boolean)=>void;onMovePole:(id:string,lng:number,lat:number)=>void;onBoxSelect:(ids:string[])=>void};

export default function MapView(p:Props){
  const container=useRef<HTMLDivElement>(null),map=useRef<MapboxMap|null>(null),markers=useRef<mapboxgl.Marker[]>([]),dataRef=useRef(p.data),callbacks=useRef(p);
  dataRef.current=p.data;callbacks.current=p;
  useEffect(()=>{
    if(!container.current||map.current||!p.token)return; mapboxgl.accessToken=p.token;
    const m=new mapboxgl.Map({container:container.current,style:p.styleUrl,center:[-52.5,-14.5],zoom:3.25,minZoom:2.8,maxBounds:[[-76,-36],[-32,7]],attributionControl:false});map.current=m;m.boxZoom.disable();
    m.addControl(new mapboxgl.NavigationControl({showCompass:false}),'bottom-right');
    m.on('load',()=>{
      m.addSource('municipalities',{type:'geojson',data:'/api/geometry/municipalities'});
      m.addLayer({id:'municipality-fill',type:'fill',source:'municipalities',paint:{'fill-color':'#152338','fill-opacity':.1}});
      m.addLayer({id:'municipality-line',type:'line',source:'municipalities',paint:{'line-color':'#8391a7','line-width':['interpolate',['linear'],['zoom'],3,.15,8,.8],'line-opacity':.45}});
      m.addSource('territories',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      m.addLayer({id:'territory-fill',type:'fill',source:'territories',paint:{'fill-color':['coalesce',['get','_color'],'#58a6ff'],'fill-opacity':['case',['boolean',['get','_visible'],false],.48,.05]}});
      m.addLayer({id:'territory-line',type:'line',source:'territories',paint:{'line-color':['case',['boolean',['get','_selected'],false],'#ffffff',['coalesce',['get','_color'],'#58a6ff']],'line-width':['case',['boolean',['get','_selected'],false],2.8,.7],'line-opacity':.9}});
      m.on('click','territory-fill',(e)=>{const id=String(e.features?.[0]?.properties?._unitId||'');if(id)callbacks.current.onUnit(id,!!e.originalEvent.ctrlKey||!!e.originalEvent.metaKey);});
      m.on('click','municipality-fill',(e)=>{const code=String(e.features?.[0]?.properties?.id||e.features?.[0]?.properties?.CD_MUN||'');const units=dataRef.current?.units.filter(x=>x.municipalityCode===code)||[];const u=units.find(x=>x.poleId===callbacks.current.selectedPole)||units[0];if(u)callbacks.current.onUnit(u.id,!!e.originalEvent.ctrlKey||!!e.originalEvent.metaKey);});
      let start:mapboxgl.Point|null=null,box:HTMLDivElement|null=null;
      m.on('mousedown',(e:MapMouseEvent)=>{if(!e.originalEvent.shiftKey)return;e.preventDefault();start=e.point;m.dragPan.disable();box=document.createElement('div');box.className='selection-box';m.getContainer().appendChild(box);});
      m.on('mousemove',(e:MapMouseEvent)=>{if(!start||!box)return;const minX=Math.min(start.x,e.point.x),minY=Math.min(start.y,e.point.y),maxX=Math.max(start.x,e.point.x),maxY=Math.max(start.y,e.point.y);Object.assign(box.style,{left:`${minX}px`,top:`${minY}px`,width:`${maxX-minX}px`,height:`${maxY-minY}px`});});
      m.on('mouseup',(e:MapMouseEvent)=>{if(!start)return;const features=m.queryRenderedFeatures([start,e.point],{layers:['territory-fill','municipality-fill']});const ids=new Set<string>();features.forEach(f=>{const direct=String(f.properties?._unitId||'');if(direct)ids.add(direct);else{const code=String(f.properties?.id||f.properties?.CD_MUN||'');dataRef.current?.units.filter(u=>u.municipalityCode===code).forEach(u=>ids.add(u.id));}});callbacks.current.onBoxSelect([...ids]);box?.remove();box=null;start=null;m.dragPan.enable();});
    });
    return()=>{m.remove();map.current=null;};
  },[p.token,p.styleUrl]);

  useEffect(()=>{
    const m=map.current;if(!m||!m.isStyleLoaded()||!p.data)return;
    markers.current.forEach(x=>x.remove());markers.current=[];
    p.data.poles.forEach(pole=>{
      const el=document.createElement('button');el.className=`pole-marker ${pole.id===p.selectedPole?'active':''}`;el.style.setProperty('--pole-color',areaColor(pole.area));el.title=`${pole.name} · ${pole.area}`;el.setAttribute('aria-label',el.title);
      const marker=new mapboxgl.Marker({element:el,draggable:p.editable}).setLngLat([pole.longitude,pole.latitude]).addTo(m);
      el.onclick=(e)=>{e.stopPropagation();p.onPole(pole.id);};
      marker.on('dragend',()=>{const q=marker.getLngLat();if(q.lng>=-74.5&&q.lng<=-34&&q.lat>=-34&&q.lat<=6)p.onMovePole(pole.id,q.lng,q.lat);else marker.setLngLat([pole.longitude,pole.latitude]);});
      markers.current.push(marker);
    });
    const base=p.data.territories.features.length?p.data.territories:municipalTerritories(p.data);
    const features=base.features.map((f:any,i)=>{const props={...(f.properties||{})};const id=String(props.DEMAND_ID||props._unitId||p.data!.units[i]?.id||'');const unit=p.data!.units.find(x=>x.id===id)||p.data!.units.find(x=>x.municipalityCode===String(props.id||props.CD_MUN||props.COD_IBGE||''));const pole=p.data!.poles.find(x=>x.id===unit?.poleId);return {...f,properties:{...props,_unitId:unit?.id||id,_poleId:unit?.poleId,_color:areaColor(pole?.area||'SEM ÁREA'),_visible:p.showAll||unit?.poleId===p.selectedPole,_selected:unit?p.selectedUnits.has(unit.id):false}};});
    (m.getSource('territories') as GeoJSONSource)?.setData({type:'FeatureCollection',features} as any);
    if(!p.data.territories.features.length){
      const colors=new Map<string,string>();const visibleCodes:string[]=[];const selectedCodes:string[]=[];
      p.data.units.forEach(u=>{const pole=p.data!.poles.find(x=>x.id===u.poleId);colors.set(u.municipalityCode,areaColor(pole?.area||'SEM ÁREA'));if(p.showAll||u.poleId===p.selectedPole)visibleCodes.push(u.municipalityCode);if(p.selectedUnits.has(u.id))selectedCodes.push(u.municipalityCode);});
      const colorPairs:any[]=[...colors].flatMap(([code,color])=>[code,color]);
      const code:any=['to-string',['coalesce',['get','CD_MUN'],['get','id'],'']];
      m.setPaintProperty('municipality-fill','fill-color',['match',code,...colorPairs,'#152338'] as any);
      m.setPaintProperty('municipality-fill','fill-opacity',['case',['in',code,['literal',visibleCodes]],.48,.08] as any);
      m.setPaintProperty('municipality-line','line-color',['case',['in',code,['literal',selectedCodes]],'#ffffff','#8391a7'] as any);
      m.setPaintProperty('municipality-line','line-width',['case',['in',code,['literal',selectedCodes]],2.8,['interpolate',['linear'],['zoom'],3,.15,8,.8]] as any);
    } else {
      m.setPaintProperty('municipality-fill','fill-color','#152338');m.setPaintProperty('municipality-fill','fill-opacity',.1);
    }
  },[p.data,p.selectedPole,p.selectedUnits,p.showAll,p.editable]);
  return <div ref={container} className="map"/>;
}

function municipalTerritories(data:ScenarioData):FeatureCollection<Geometry>{
  // Current portfolios use the shared municipal layer; this empty source keeps the scenario contract uniform.
  return {type:'FeatureCollection',features:[]};
}
