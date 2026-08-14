import { useEffect, useMemo, useRef, useState } from 'react';
import mapboxgl, { type GeoJSONSource, type Map as MapboxMap, type MapMouseEvent } from 'mapbox-gl';
import type { FeatureCollection, Geometry } from 'geojson';
import type { RegionalOffice, ScenarioData } from '../shared/types';
import type { PoleMovement } from '../shared/scenarioComparison';
import { populationBandIndex, POPULATION_BANDS } from '../shared/population';
import { areaColor, EXCLUDED_MUNICIPALITY_COLOR, EXCLUDED_MUNICIPALITY_STROKE, lightenColor, markerColor, PENDING_ASSIGN_COLOR, PENDING_ASSIGN_STROKE, shadeColor, territoryColor } from '../shared/mapColors';
import { circlePolygon } from '../shared/geo';

type Props={
  data:ScenarioData|null;
  token:string;
  styleUrl:string;
  selectedPole:string|null;
  selectedUnits:Set<string>;
  waveUnitId:string|null;
  selectedArea:string|null;
  showAll:boolean;
  showPopulation:boolean;
  population:Record<string,number>;
  showExcluded:boolean;
  excludedCodes:string[];
  showRegionals:boolean;
  regionals:RegionalOffice[];
  showPoles:boolean;
  comparisonPoles:ScenarioData['poles'];
  movements:PoleMovement[];
  showComparisonPoles:boolean;
  showMovementLines:boolean;
  editable:boolean;
  radiusKm:number;
  onPole:(id:string)=>void;
  onUnit:(id:string,additive:boolean)=>void;
  onStageMeshMunicipality:(payload:{
    unitId?:string;
    municipalityCode:string;
    name:string;
    latitude:number;
    longitude:number;
    population:number;
  })=>void;
  onStageMeshDistrict:(payload:{
    unitId?:string;
    districtCode:string;
    municipalityCode:string;
    name:string;
    municipalityName?:string;
    latitude:number;
    longitude:number;
    population:number;
  })=>void;
  onMovePole:(id:string,lng:number,lat:number)=>void;
  onBoxSelect:(ids:string[])=>void;
};

const codeExpression:any=['to-string',['coalesce',['get','CD_MUN'],['get','id'],['get','COD_IBGE'],'']];
const districtCodeExpression:any=['to-string',['coalesce',['get','CD_DIST'],'']];
const distCode=(value:unknown)=>String(value||'').replace(/\D/g,'');
const STANDARD_STYLE='mapbox://styles/mapbox/standard';
const WAVE_PERIOD_MS=5200;
const WAVE_MAX_OFFSET_M=14000;
const WAVE_FRONTS=2;
const MARKER_OVERLAP_RADIUS_PX=12;

const emptyPoints=():FeatureCollection=>({type:'FeatureCollection',features:[]});
const poleFeatures=(m:MapboxMap,p:Props,moved?:{id:string;longitude:number;latitude:number})=>{
  if(!p.showPoles||!p.data)return[];
  const visibleRegionals=p.showRegionals?p.regionals:[];
  return p.data.poles
    .filter(pole=>!p.selectedArea||pole.area===p.selectedArea)
    .map(pole=>{
      const longitude=pole.id===moved?.id?moved.longitude:pole.longitude;
      const latitude=pole.id===moved?.id?moved.latitude:pole.latitude;
      const point=m.project([longitude,latitude]);
      const overlapsRegional=visibleRegionals.some(regional=>{
        const regionalPoint=m.project([regional.longitude,regional.latitude]);
        return Math.hypot(point.x-regionalPoint.x,point.y-regionalPoint.y)<=MARKER_OVERLAP_RADIUS_PX;
      });
      return{
        type:'Feature' as const,
        properties:{
          id:pole.id,
          color:markerColor(pole,p.data!.poles),
          ring:territoryColor(pole,p.data!.poles),
          active:pole.id===p.selectedPole?1:0,
          overlapsRegional:overlapsRegional?1:0,
        },
        geometry:{type:'Point' as const,coordinates:[longitude,latitude]},
      };
    });
};
const updatePoleSource=(m:MapboxMap,p:Props,moved?:{id:string;longitude:number;latitude:number})=>{
  (m.getSource('poles') as GeoJSONSource|undefined)?.setData({type:'FeatureCollection',features:poleFeatures(m,p,moved)});
};
const munCode=(value:unknown)=>String(value||'').replace(/\D/g,'').padStart(7,'0').slice(-7);
const metersPerDeg=(lat:number)=>{
  const cos=Math.cos((lat*Math.PI)/180);
  return{x:111320*Math.max(0.2,Math.abs(cos)),y:110540};
};
const toXY=(lng:number,lat:number,lat0:number):[number,number]=>{
  const m=metersPerDeg(lat0);
  return[lng*m.x,lat*m.y];
};
const toLngLat=(x:number,y:number,lat0:number):[number,number]=>{
  const m=metersPerDeg(lat0);
  return[x/m.x,y/m.y];
};
const normalize2=(x:number,y:number):[number,number]=>{
  const len=Math.hypot(x,y);
  return len>1e-9?[x/len,y/len]:[0,0];
};
/** Empurra o anel para fora por distância fixa em metros (onda partindo do contorno). */
const offsetRingOutward=(ring:number[][],distanceMeters:number):number[][]=>{
  if(!ring.length||distanceMeters===0)return ring.map(point=>[point[0],point[1]]);
  const closed=ring.length>1&&ring[0][0]===ring[ring.length-1][0]&&ring[0][1]===ring[ring.length-1][1];
  const pts=closed?ring.slice(0,-1):ring.slice();
  if(pts.length<3)return ring.map(point=>[point[0],point[1]]);
  const lat0=pts.reduce((sum,point)=>sum+point[1],0)/pts.length;
  const xy=pts.map(([lng,lat])=>toXY(lng,lat,lat0));
  let area=0;
  for(let i=0;i<xy.length;i++){
    const j=(i+1)%xy.length;
    area+=xy[i][0]*xy[j][1]-xy[j][0]*xy[i][1];
  }
  // GeoJSON exterior = CCW → interior à esquerda → fora = normal à direita (dy,-dx)
  const outward=area>=0?1:-1;
  const result:number[][]=[];
  for(let i=0;i<xy.length;i++){
    const prev=xy[(i-1+xy.length)%xy.length];
    const curr=xy[i];
    const next=xy[(i+1)%xy.length];
    const [inX,inY]=normalize2(curr[0]-prev[0],curr[1]-prev[1]);
    const [outX,outY]=normalize2(next[0]-curr[0],next[1]-curr[1]);
    const n1=normalize2(inY*outward,-inX*outward);
    const n2=normalize2(outY*outward,-outX*outward);
    let [nx,ny]=normalize2(n1[0]+n2[0],n1[1]+n2[1]);
    if(!nx&&!ny){[nx,ny]=n2;}
    const cos=Math.max(0.25,n1[0]*nx+n1[1]*ny);
    const miter=Math.min(2.5,1/cos);
    result.push(toLngLat(curr[0]+nx*distanceMeters*miter,curr[1]+ny*distanceMeters*miter,lat0));
  }
  result.push([result[0][0],result[0][1]]);
  return result;
};
const offsetGeometryOutward=(geometry:any,distanceMeters:number):any=>{
  if(!geometry||distanceMeters<=0)return geometry;
  if(geometry.type==='Polygon'){
    // Só o anel externo viaja como onda; buracos ficam de fora
    return{type:'Polygon',coordinates:[offsetRingOutward(geometry.coordinates[0],distanceMeters)]};
  }
  if(geometry.type==='MultiPolygon'){
    return{
      type:'MultiPolygon',
      coordinates:geometry.coordinates.map((polygon:number[][][])=>[offsetRingOutward(polygon[0],distanceMeters)]),
    };
  }
  return geometry;
};
const inBrazil=(lng:number,lat:number)=>lng>=-74.5&&lng<=-34&&lat>=-34&&lat<=6;
const isBrazilLabel=(value:unknown)=>{
  const name=String(value||'').trim().toLowerCase();
  return name==='brazil'||name==='brasil';
};

let brazilRings:number[][][]|null=null;
const loadBrazilRings=()=>{
  if(brazilRings)return Promise.resolve(brazilRings);
  return fetch('/brazil-mask.json')
    .then(res=>res.json())
    .then((geo:any)=>{
      const mask=geo?.features?.find((f:any)=>f?.properties?.kind==='mask');
      const rings=mask?.geometry?.coordinates?.slice(1);
      brazilRings=Array.isArray(rings)?rings:null;
      return brazilRings;
    })
    .catch(()=>null);
};

/** Ray-casting: ponto dentro de algum anel (buracos do mask = Brasil). */
const pointInRing=(lng:number,lat:number,ring:number[][])=>{
  let inside=false;
  for(let i=0,j=ring.length-1;i<ring.length;j=i++){
    const xi=ring[i][0],yi=ring[i][1];
    const xj=ring[j][0],yj=ring[j][1];
    if(((yi>lat)!==(yj>lat))&&(lng<(xj-xi)*(lat-yi)/((yj-yi)||1e-12)+xi))inside=!inside;
  }
  return inside;
};
const pointInBrazil=(lng:number,lat:number)=>{
  if(brazilRings?.length)return brazilRings.some(ring=>pointInRing(lng,lat,ring));
  return inBrazil(lng,lat);
};

/** Standard: place labels renderizam acima do slot top — clip + feature-state hide. */
const ensureWorldLabelClip=(m:MapboxMap)=>{
  if(m.getLayer('world-label-clip'))return;
  if(!m.getSource('brazil-mask'))return;
  try{
    m.addLayer({
      id:'world-label-clip',
      type:'clip',
      source:'brazil-mask',
      filter:['==',['get','kind'],'mask'],
      layout:{'clip-layer-types':['symbol']},
    } as any);
  }catch{/* clip indisponível */}
};

const hideForeignStandardPlaceLabels=(m:MapboxMap)=>{
  try{
    const features=m.queryRenderedFeatures({target:{featuresetId:'place-labels',importId:'basemap'}} as any) as any[];
    for(const feature of features||[]){
      const props=feature.properties||{};
      const klass=String(props.class||'');
      let hide=false;
      if(klass==='country'||klass==='continent')hide=true;
      else if(feature.geometry?.type==='Point'){
        const [lng,lat]=feature.geometry.coordinates;
        hide=!pointInBrazil(lng,lat);
      }else if(!isBrazilLabel(props.name)&&!isBrazilLabel(props.name_en)&&!isBrazilLabel(props.name_pt)){
        hide=true;
      }
      try{m.setFeatureState(feature,{hide});}catch{/* */}
    }
  }catch{/* featureset indisponível */}
};

/** Estilos clássicos: limita rótulos de lugar ao Brasil. */
const limitClassicPlaceLabelsToBrazil=(m:MapboxMap)=>{
  const brazilOnly:any=['any',
    ['==',['get','iso_3166_1'],'BR'],
    ['==',['downcase',['to-string',['coalesce',['get','name_en'],['get','name'],'']]],'brazil'],
    ['==',['downcase',['to-string',['coalesce',['get','name'],'']]],'brasil'],
  ];
  for(const layer of m.getStyle()?.layers||[]){
    if(layer.type!=='symbol')continue;
    if(layer.id==='regionals-label')continue;
    if(!/(country|place-label|settlement|state-label|state_label)/i.test(layer.id))continue;
    try{
      const current=m.getFilter(layer.id) as any;
      const base=current&&current!==true?current:true;
      // Evita empilhar o mesmo filtro a cada reload
      const already=JSON.stringify(base).includes('"iso_3166_1"');
      if(already)continue;
      m.setFilter(layer.id,(base===true?brazilOnly:['all',base,brazilOnly]) as any);
    }catch{/* layer gerenciada */}
  }
};

export default function MapView(p:Props){
  const container=useRef<HTMLDivElement>(null);
  const map=useRef<MapboxMap|null>(null);
  const popup=useRef<mapboxgl.Popup|null>(null);
  const dataRef=useRef(p.data);
  const callbacks=useRef(p);
  const dragPole=useRef<{id:string;moved:boolean}|null>(null);
  const [styleReady,setStyleReady]=useState(false);
  const [showMesh,setShowMesh]=useState(false);
  const [showDistrictMesh,setShowDistrictMesh]=useState(false);
  const [useStandard,setUseStandard]=useState(true);
  const [meshEpoch,setMeshEpoch]=useState(0);
  const meshLoaded=useRef(false);
  const districtMeshLoaded=useRef(false);
  const showMeshRef=useRef(false);
  const showDistrictMeshRef=useRef(false);
  const meshByCode=useRef(new Map<string,any>());
  const waveBases=useRef<any[]>([]);
  const waveColor=useRef('#39d98a');
  const pulseFrame=useRef(0);
  const selectedKey=p.waveUnitId||'';
  const poles=useMemo(()=>p.data?.poles||[],[p.data]);
  const colorForMarker=(pole:ScenarioData['poles'][number])=>markerColor(pole,poles);
  const colorForTerritory=(pole:ScenarioData['poles'][number])=>territoryColor(pole,poles);
  const activeStyle=useStandard?STANDARD_STYLE:p.styleUrl;
  dataRef.current=p.data;
  callbacks.current=p;
  showMeshRef.current=showMesh;
  showDistrictMeshRef.current=showDistrictMesh;

  const stopWave=()=>{
    if(pulseFrame.current)cancelAnimationFrame(pulseFrame.current);
    pulseFrame.current=0;
  };
  const clearWave=(m:MapboxMap)=>{
    stopWave();
    waveBases.current=[];
    (m.getSource('selection-wave') as GeoJSONSource|undefined)?.setData({type:'FeatureCollection',features:[]});
  };
  const paintWave=(m:MapboxMap,now:number)=>{
    const bases=waveBases.current;
    const source=m.getSource('selection-wave') as GeoJSONSource|undefined;
    if(!source)return;
    if(!bases.length){
      source.setData({type:'FeatureCollection',features:[]});
      return;
    }
    const color=waveColor.current;
    const features:any[]=[];
    for(let front=0;front<WAVE_FRONTS;front++){
      const phase=((now/WAVE_PERIOD_MS)+front/WAVE_FRONTS)%1;
      // ease suave: sai do contorno e avança para fora sem “estourar”
      const eased=phase*phase*(3-2*phase);
      const distance=eased*WAVE_MAX_OFFSET_M;
      const opacity=Math.max(0,(1-phase)*0.85);
      const width=2+(1-phase)*1.6;
      for(const geometry of bases){
        features.push({
          type:'Feature',
          properties:{opacity,width,color},
          geometry:offsetGeometryOutward(geometry,distance),
        });
      }
    }
    source.setData({type:'FeatureCollection',features});
  };
  const startWave=(m:MapboxMap)=>{
    stopWave();
    const tick=(now:number)=>{
      paintWave(m,now);
      pulseFrame.current=requestAnimationFrame(tick);
    };
    pulseFrame.current=requestAnimationFrame(tick);
  };

  useEffect(()=>{
    if(!container.current||map.current||!p.token)return;
    mapboxgl.accessToken=p.token;
    const m=new mapboxgl.Map({
      container:container.current,
      style:activeStyle,
      projection:'mercator',
      center:[-52.5,-14.8],
      zoom:2.8,
      minZoom:1.8,
      maxBounds:[[-95,-48],[-10,18]],
      attributionControl:false,
    });
    map.current=m;
    m.boxZoom.disable();
    m.addControl(new mapboxgl.NavigationControl({showCompass:false}),'bottom-right');
    const lockMercator=()=>m.setProjection('mercator');
    const isStandardStyle=activeStyle===STANDARD_STYLE;
    const raiseWorldMask=()=>{
      if(!m.getLayer('world-mask'))return;
      ensureWorldLabelClip(m);
      m.moveLayer('world-mask');
      for(const id of [
        'municipality-fill','portfolio-fill','portfolio-outline','portfolio-stitch',
        'territory-fill','territory-outline','territory-stitch','municipality-line',
        'district-fill','district-line-halo','district-line',
        'selection-wave',
        'brazil-outline','state-outline','comparison-movements','current-poles-circle',
        'radius-fill','radius-outline','poles-circle',
        'regionals-circle','regionals-label',
      ]){
        if(m.getLayer(id))m.moveLayer(id);
      }
      // Clip precisa ficar no topo para afetar place labels do basemap (abaixo dele).
      if(m.getLayer('world-label-clip'))m.moveLayer('world-label-clip');
    };
    const suppressForeignLabels=()=>{
      ensureWorldLabelClip(m);
      if(isStandardStyle)hideForeignStandardPlaceLabels(m);
      else limitClassicPlaceLabelsToBrazil(m);
      raiseWorldMask();
    };
    m.on('style.load',()=>{lockMercator();suppressForeignLabels();});
    m.on('moveend',()=>{if(isStandardStyle)hideForeignStandardPlaceLabels(m);});
    m.on('idle',()=>raiseWorldMask());
    loadBrazilRings().then(()=>{if(isStandardStyle)hideForeignStandardPlaceLabels(m);});
    m.on('load',()=>{
      lockMercator();
      m.addSource('brazil-mask',{type:'geojson',data:'/brazil-mask.json'});
      m.addLayer({
        id:'world-mask',
        type:'fill',
        source:'brazil-mask',
        ...(isStandardStyle?{slot:'top' as const}:{}),
        filter:['==',['get','kind'],'mask'],
        paint:{'fill-color':'#e5e7eb','fill-opacity':1},
      });
      ensureWorldLabelClip(m);
      m.addSource('municipalities',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      m.addLayer({id:'municipality-fill',type:'fill',source:'municipalities',...(isStandardStyle?{slot:'top' as const}:{}),paint:{'fill-color':'#152338','fill-opacity':.1}});
      m.addLayer({id:'portfolio-fill',type:'fill',source:'municipalities',...(isStandardStyle?{slot:'top' as const}:{}),paint:{'fill-color':'#38bdf8','fill-opacity':0}});
      m.addLayer({id:'municipality-line',type:'line',source:'municipalities',...(isStandardStyle?{slot:'top' as const}:{}),layout:{visibility:'none'},paint:{'line-color':'#8391a7','line-width':['interpolate',['linear'],['zoom'],3,.15,8,.8],'line-opacity':.55}});
      // Contorno da mancha: base na cor + tracejado mais escuro (- - - - -)
      m.addLayer({id:'portfolio-outline',type:'line',source:'municipalities',...(isStandardStyle?{slot:'top' as const}:{}),layout:{visibility:'none','line-join':'round','line-cap':'round'},paint:{
        'line-color':'#38bdf8',
        'line-width':['interpolate',['linear'],['zoom'],3,1.6,8,2.6],
        'line-opacity':0,
      }});
      m.addLayer({id:'portfolio-stitch',type:'line',source:'municipalities',...(isStandardStyle?{slot:'top' as const}:{}),layout:{visibility:'none','line-join':'round','line-cap':'butt'},paint:{
        'line-color':'#1d4f7a',
        'line-width':['interpolate',['linear'],['zoom'],3,1.4,8,2.2],
        'line-opacity':0,
        'line-dasharray':[2.4,2.2],
      }});
      m.addSource('selection-wave',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      m.addLayer({id:'selection-wave',type:'line',source:'selection-wave',...(isStandardStyle?{slot:'top' as const}:{}),layout:{'line-join':'round','line-cap':'round'},paint:{
        'line-color':['coalesce',['get','color'],'#39d98a'],
        'line-width':['coalesce',['get','width'],2.4],
        'line-opacity':['coalesce',['get','opacity'],0],
        'line-blur':1.2,
      }});
      m.addLayer({id:'brazil-outline',type:'line',source:'brazil-mask',...(isStandardStyle?{slot:'top' as const}:{}),filter:['==',['get','kind'],'outline'],paint:{'line-color':'#000000','line-width':['interpolate',['linear'],['zoom'],1.8,.8,7,1.8],'line-opacity':.55}});
      m.addLayer({id:'state-outline',type:'line',source:'brazil-mask',...(isStandardStyle?{slot:'top' as const}:{}),filter:['==',['get','kind'],'states'],paint:{'line-color':'#000000','line-width':['interpolate',['linear'],['zoom'],1.8,.8,7,1.8],'line-opacity':.55}});
      m.addSource('territories',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      m.addLayer({id:'territory-fill',type:'fill',source:'territories',...(isStandardStyle?{slot:'top' as const}:{}),paint:{'fill-color':['coalesce',['get','_color'],'#58a6ff'],'fill-opacity':['case',['boolean',['get','_visible'],false],.28,.02]}});
      m.addLayer({id:'territory-outline',type:'line',source:'territories',...(isStandardStyle?{slot:'top' as const}:{}),layout:{'line-join':'round','line-cap':'round'},paint:{
        'line-color':['coalesce',['get','_color'],'#58a6ff'],
        'line-width':['case',['boolean',['get','_visible'],false],['interpolate',['linear'],['zoom'],3,1.6,8,2.6],0],
        'line-opacity':['case',['boolean',['get','_visible'],false],.7,0],
      }});
      m.addLayer({id:'territory-stitch',type:'line',source:'territories',...(isStandardStyle?{slot:'top' as const}:{}),layout:{'line-join':'round','line-cap':'butt'},paint:{
        'line-color':['coalesce',['get','_stitch'],'#2a5a8c'],
        'line-width':['case',['boolean',['get','_visible'],false],['interpolate',['linear'],['zoom'],3,1.4,8,2.2],0],
        'line-opacity':['case',['boolean',['get','_visible'],false],.95,0],
        'line-dasharray':[2.4,2.2],
      }});
      // Distritos acima dos territórios para hit-test/clique confiável.
      m.addSource('districts',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      m.addLayer({id:'district-fill',type:'fill',source:'districts',...(isStandardStyle?{slot:'top' as const}:{}),layout:{visibility:'none'},paint:{
        'fill-color':'#f59e0b',
        'fill-opacity':0.18,
      }});
      m.addLayer({id:'district-line-halo',type:'line',source:'districts',...(isStandardStyle?{slot:'top' as const}:{}),layout:{visibility:'none','line-join':'round','line-cap':'round'},paint:{
        'line-color':'#0b1220',
        'line-width':['interpolate',['linear'],['zoom'],4,1.6,8,3.4,12,5],
        'line-opacity':0.85,
      }});
      m.addLayer({id:'district-line',type:'line',source:'districts',...(isStandardStyle?{slot:'top' as const}:{}),layout:{visibility:'none','line-join':'round','line-cap':'round'},paint:{
        'line-color':'#fbbf24',
        'line-width':['interpolate',['linear'],['zoom'],4,0.9,8,2.1,12,3.2],
        'line-opacity':0.98,
      }});
      m.addSource('comparison-movements',{type:'geojson',data:emptyPoints()});
      m.addLayer({id:'comparison-movements',type:'line',source:'comparison-movements',...(isStandardStyle?{slot:'top' as const}:{}),layout:{'line-join':'round','line-cap':'round'},paint:{
        'line-color':['get','color'],
        'line-width':['interpolate',['linear'],['zoom'],3,1,8,2.2],
        'line-opacity':0.72,
        'line-dasharray':[2.4,1.8],
      }});
      m.addSource('current-poles',{type:'geojson',data:emptyPoints()});
      m.addLayer({id:'current-poles-circle',type:'circle',source:'current-poles',...(isStandardStyle?{slot:'top' as const}:{}),paint:{
        'circle-radius':9,
        'circle-color':['get','color'],
        'circle-stroke-width':1.5,
        'circle-stroke-color':'#ffffff',
        'circle-opacity':0.72,
      }});
      m.addSource('poles',{type:'geojson',data:emptyPoints()});
      m.addLayer({id:'poles-circle',type:'circle',source:'poles',...(isStandardStyle?{slot:'top' as const}:{}),paint:{
        'circle-radius':['case',
          ['==',['get','overlapsRegional'],1],['case',['==',['get','active'],1],16,14],
          ['==',['get','active'],1],9,
          7,
        ],
        'circle-color':['get','color'],
        'circle-stroke-width':['case',['==',['get','active'],1],3,2],
        'circle-stroke-color':['case',['==',['get','active'],1],['get','ring'],'#09111f'],
        'circle-opacity':1,
      }});
      m.addSource('radius-circle',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      m.addLayer({id:'radius-fill',type:'fill',source:'radius-circle',...(isStandardStyle?{slot:'top' as const}:{}),paint:{
        'fill-color':'#38bdf8',
        'fill-opacity':0.12,
      }});
      m.addLayer({id:'radius-outline',type:'line',source:'radius-circle',...(isStandardStyle?{slot:'top' as const}:{}),layout:{'line-join':'round','line-cap':'round'},paint:{
        'line-color':'#38bdf8',
        'line-width':2,
        'line-opacity':0.85,
        'line-dasharray':[2,1.6],
      }});
      m.addSource('regionals',{type:'geojson',data:emptyPoints()});
      m.addLayer({id:'regionals-circle',type:'circle',source:'regionals',...(isStandardStyle?{slot:'top' as const}:{}),paint:{
        'circle-radius':9,
        'circle-color':'#0d3b66',
        'circle-stroke-width':2,
        'circle-stroke-color':'#ffffff',
      }});
      m.addLayer({id:'regionals-label',type:'symbol',source:'regionals',...(isStandardStyle?{slot:'top' as const}:{}),layout:{
        'text-field':'GR',
        'text-size':8,
        'text-font':['Open Sans Bold','Arial Unicode MS Bold'],
        'text-allow-overlap':true,
        'text-ignore-placement':true,
      },paint:{'text-color':'#ffffff'}});
      suppressForeignLabels();
      const districtLayers=()=>['district-fill','district-line-halo','district-line'].filter(id=>!!m.getLayer(id));
      const openDistrictPopup=(feature:mapboxgl.MapboxGeoJSONFeature,lngLat:mapboxgl.LngLat)=>{
        const name=String(feature.properties?.NM_DIST||`Distrito ${feature.properties?.CD_DIST||''}`).trim()||'Distrito';
        const population=Math.max(0,Number(feature.properties?.POP_2022)||0);
        popup.current?.remove();
        popup.current=new mapboxgl.Popup({closeButton:true,closeOnClick:true,offset:12,maxWidth:'280px'})
          .setLngLat(lngLat)
          .setDOMContent(districtCard({name,population}))
          .addTo(m);
      };
      const tryShowDistrict=(point:mapboxgl.Point,lngLat:mapboxgl.LngLat,originalEvent?:Event)=>{
        // Layer + click global disparam no mesmo clique; evita toggle off da seleção.
        if(originalEvent&&'__districtHandled' in (originalEvent as any)){
          return !!(originalEvent as any).__districtHandled;
        }
        if(!showDistrictMeshRef.current){
          if(originalEvent)(originalEvent as any).__districtHandled=false;
          return false;
        }
        const layers=districtLayers();
        if(!layers.length){
          if(originalEvent)(originalEvent as any).__districtHandled=false;
          return false;
        }
        const feature=m.queryRenderedFeatures(point,{layers})[0];
        if(!feature){
          if(originalEvent)(originalEvent as any).__districtHandled=false;
          return false;
        }
        if(originalEvent)(originalEvent as any).__districtHandled=true;
        const districtCode=distCode(feature.properties?.CD_DIST);
        const municipalityCode=munCode(feature.properties?.CD_MUN);
        const name=String(feature.properties?.NM_DIST||`Distrito ${districtCode}`).trim()||'Distrito';
        const municipalityName=String(feature.properties?.NM_MUN||'').trim()||undefined;
        const population=Math.max(0,Number(feature.properties?.POP_2022)||0);
        openDistrictPopup(feature,lngLat);
        // Builder + malha distrital + polo: pré-seleciona o distrito.
        if(callbacks.current.editable&&callbacks.current.selectedPole&&districtCode){
          const existing=dataRef.current?.units.find(unit=>unit.type==='DISTRITO'&&distCode(unit.districtCode)===districtCode);
          callbacks.current.onStageMeshDistrict({
            unitId:existing?.id,
            districtCode,
            municipalityCode,
            name,
            municipalityName,
            latitude:existing?.latitude??lngLat.lat,
            longitude:existing?.longitude??lngLat.lng,
            population,
          });
        }
        return true;
      };
      const showMunicipality=(e:mapboxgl.MapLayerMouseEvent)=>{
        // Prioriza marker de polo/regional: não seleciona o município sob o ponto.
        if(m.queryRenderedFeatures(e.point,{layers:['poles-circle','current-poles-circle','regionals-circle']}).length)return;
        // Malha distrital ativa: abre o card do distrito (não o do município).
        if(tryShowDistrict(e.point,e.lngLat,e.originalEvent))return;
        const feature=e.features?.[0];
        if(!feature)return;
        const directId=String(feature.properties?._unitId||'');
        const direct=dataRef.current?.units.find(unit=>unit.id===directId);
        const code=munCode(feature.properties?.id||feature.properties?.CD_MUN||feature.properties?.COD_IBGE||direct?.municipalityCode||'');
        const units=dataRef.current?.units.filter(unit=>munCode(unit.municipalityCode)===code)||[];
        const selectedPoleId=callbacks.current.selectedPole;
        const unit=direct
          ||units.find(item=>item.poleId===selectedPoleId)
          ||units.find(item=>item.type==='MUNICIPIO')
          ||units[0];

        // Builder + malha + polo: pré-seleciona (cor pendente). Atribuir confirma e unifica.
        if(callbacks.current.editable&&showMeshRef.current&&selectedPoleId){
          const name=String(unit?.municipalityName||feature.properties?.name||feature.properties?.NM_MUN||feature.properties?.description||`Município ${code}`);
          const population=callbacks.current.population[code]??Math.max(0,...units.map(item=>item.population||0),unit?.population||0);
          const stores=units.reduce((total,item)=>total+(item.stores||0),0);
          const assignedPole=dataRef.current?.poles.find(item=>item.id===unit?.poleId);
          popup.current?.remove();
          popup.current=new mapboxgl.Popup({closeButton:true,closeOnClick:true,offset:12,maxWidth:'300px'})
            .setLngLat(e.lngLat)
            .setDOMContent(municipalityCard({name,code,population,stores,pole:assignedPole?.name||'Sem polo atribuído'}))
            .addTo(m);
          callbacks.current.onStageMeshMunicipality({
            unitId:unit?.id,
            municipalityCode:code,
            name,
            latitude:unit?.latitude??e.lngLat.lat,
            longitude:unit?.longitude??e.lngLat.lng,
            population:callbacks.current.population[code]??unit?.population??0,
          });
          return;
        }

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
      // Clique global: garante hit no distrito mesmo quando outra fill está acima.
      m.on('click',(e:MapMouseEvent)=>{
        if(e.originalEvent.shiftKey)return;
        if(m.queryRenderedFeatures(e.point,{layers:['poles-circle','current-poles-circle','regionals-circle']}).length)return;
        tryShowDistrict(e.point,e.lngLat,e.originalEvent);
      });
      m.on('click','territory-fill',showMunicipality);
      m.on('click','portfolio-fill',showMunicipality);
      m.on('click','municipality-fill',(e)=>{
        if(m.queryRenderedFeatures(e.point,{layers:['territory-fill','portfolio-fill']}).length)return;
        showMunicipality(e);
      });
      m.on('click','poles-circle',(e)=>{
        e.originalEvent.stopPropagation();
        // No Builder o polo é selecionado no mouseup (para permitir arrastar).
        if(callbacks.current.editable)return;
        const id=String(e.features?.[0]?.properties?.id||'');
        if(id)callbacks.current.onPole(id);
      });
      m.on('click','regionals-circle',(e)=>{
        e.originalEvent.stopPropagation();
        const id=String(e.features?.[0]?.properties?.id||'');
        const regional=callbacks.current.regionals.find(item=>item.id===id);
        if(!regional)return;
        popup.current?.remove();
        popup.current=new mapboxgl.Popup({closeButton:true,closeOnClick:true,offset:18,maxWidth:'320px'})
          .setLngLat([regional.longitude,regional.latitude])
          .setDOMContent(regionalCard(regional))
          .addTo(m);
      });
      m.on('click','current-poles-circle',(e)=>{
        e.originalEvent.stopPropagation();
        const id=String(e.features?.[0]?.properties?.id||'');
        const pole=callbacks.current.comparisonPoles.find(item=>item.id===id);
        if(!pole)return;
        popup.current?.remove();
        popup.current=new mapboxgl.Popup({closeButton:true,closeOnClick:true,offset:14,maxWidth:'300px'})
          .setLngLat([pole.longitude,pole.latitude])
          .setDOMContent(currentPoleCard(pole))
          .addTo(m);
      });
      m.on('mousedown','poles-circle',(e)=>{
        e.originalEvent.stopPropagation();
        if(!callbacks.current.editable||e.originalEvent.shiftKey)return;
        const id=String(e.features?.[0]?.properties?.id||'');
        if(!id)return;
        e.preventDefault();
        dragPole.current={id,moved:false};
        m.dragPan.disable();
      });
      m.on('mousemove',(e:MapMouseEvent)=>{
        const drag=dragPole.current;
        if(!drag)return;
        drag.moved=true;
        updatePoleSource(m,callbacks.current,{id:drag.id,longitude:e.lngLat.lng,latitude:e.lngLat.lat});
      });
      m.on('mouseup',(e:MapMouseEvent)=>{
        const drag=dragPole.current;
        if(!drag)return;
        dragPole.current=null;
        m.dragPan.enable();
        if(!drag.moved){callbacks.current.onPole(drag.id);return;}
        if(inBrazil(e.lngLat.lng,e.lngLat.lat))callbacks.current.onMovePole(drag.id,e.lngLat.lng,e.lngLat.lat);
        else{
          const pole=dataRef.current?.poles.find(item=>item.id===drag.id);
          if(pole)updatePoleSource(m,callbacks.current);
        }
      });
      m.on('moveend',()=>updatePoleSource(m,callbacks.current));
      for(const layer of ['district-fill','territory-fill','portfolio-fill','municipality-fill','poles-circle','current-poles-circle','regionals-circle']){
        m.on('mouseenter',layer,()=>{m.getCanvas().style.cursor=callbacks.current.editable&&layer==='poles-circle'?'grab':'pointer';});
        m.on('mouseleave',layer,()=>{if(!dragPole.current)m.getCanvas().style.cursor='';});
      }
      // Cursor também nas linhas da malha distrital (mais fáceis de mirar).
      for(const layer of ['district-line','district-line-halo']){
        m.on('mouseenter',layer,()=>{m.getCanvas().style.cursor='pointer';});
        m.on('mouseleave',layer,()=>{if(!dragPole.current)m.getCanvas().style.cursor='';});
      }
      let start:mapboxgl.Point|null=null,box:HTMLDivElement|null=null;
      m.on('mousedown',(e:MapMouseEvent)=>{if(!e.originalEvent.shiftKey)return;e.preventDefault();start=e.point;m.dragPan.disable();box=document.createElement('div');box.className='selection-box';m.getContainer().appendChild(box);});
      m.on('mousemove',(e:MapMouseEvent)=>{if(!start||!box)return;const minX=Math.min(start.x,e.point.x),minY=Math.min(start.y,e.point.y),maxX=Math.max(start.x,e.point.x),maxY=Math.max(start.y,e.point.y);Object.assign(box.style,{left:`${minX}px`,top:`${minY}px`,width:`${maxX-minX}px`,height:`${maxY-minY}px`});});
      m.on('mouseup',(e:MapMouseEvent)=>{if(!start)return;const features=m.queryRenderedFeatures([start,e.point],{layers:['territory-fill','portfolio-fill','municipality-fill']});const ids=new Set<string>();features.forEach(f=>{const direct=String(f.properties?._unitId||'');if(direct)ids.add(direct);else{const code=String(f.properties?.id||f.properties?.CD_MUN||'').padStart(7,'0');dataRef.current?.units.filter(u=>u.municipalityCode===code||u.municipalityCode===String(f.properties?.id||f.properties?.CD_MUN||'')).forEach(u=>ids.add(u.id));}});callbacks.current.onBoxSelect([...ids]);box?.remove();box=null;start=null;m.dragPan.enable();});
      setStyleReady(true);
    });
    return()=>{clearWave(m);setStyleReady(false);meshLoaded.current=false;districtMeshLoaded.current=false;meshByCode.current=new Map();popup.current?.remove();popup.current=null;m.remove();map.current=null;};
  },[p.token,activeStyle]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady)return;
    // Mancha da carteira: polo selecionado ou "Mostrar todas as áreas" — não ao filtrar gerência.
    const needPortfolio=!!(p.selectedPole||p.showAll);
    const needData=showMesh||p.showPopulation||p.showExcluded||needPortfolio;
    const setMunicipalVisibility=()=>{
      if(m.getLayer('municipality-line'))m.setLayoutProperty('municipality-line','visibility',showMesh||p.showExcluded?'visible':'none');
      if(m.getLayer('municipality-fill'))m.setLayoutProperty('municipality-fill','visibility',p.showPopulation||p.showExcluded||showMesh?'visible':'none');
      if(m.getLayer('portfolio-fill'))m.setLayoutProperty('portfolio-fill','visibility',needPortfolio||p.showPopulation||p.showExcluded||showMesh?'visible':'none');
      if(m.getLayer('portfolio-outline'))m.setLayoutProperty('portfolio-outline','visibility',needPortfolio||p.showExcluded?'visible':'none');
      if(m.getLayer('portfolio-stitch'))m.setLayoutProperty('portfolio-stitch','visibility',needPortfolio||p.showExcluded?'visible':'none');
    };
    if(!needData){
      setMunicipalVisibility();
      return;
    }
    if(meshLoaded.current){
      setMunicipalVisibility();
      return;
    }
    meshLoaded.current=true;
    fetch('/api/geometry/municipalities')
      .then(res=>{if(!res.ok)throw new Error(`Malha municipal: ${res.status}`);return res.json();})
      .then(geojson=>{
        const byCode=new Map<string,any>();
        for(const feature of geojson.features||[]){
          const code=munCode(feature?.properties?.CD_MUN||feature?.properties?.id||feature?.properties?.COD_IBGE);
          if(code&&code!=='0000000'&&feature.geometry)byCode.set(code,feature.geometry);
        }
        meshByCode.current=byCode;
        (m.getSource('municipalities') as GeoJSONSource)?.setData(geojson);
        setMunicipalVisibility();
        setMeshEpoch(value=>value+1);
      })
      .catch(()=>{meshLoaded.current=false;});
  },[showMesh,p.showPopulation,p.showExcluded,p.selectedPole,p.showAll,styleReady]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady)return;
    const setDistrictVisibility=()=>{
      const visibility=showDistrictMesh?'visible':'none';
      for(const id of ['district-fill','district-line-halo','district-line']){
        if(m.getLayer(id))m.setLayoutProperty(id,'visibility',visibility);
      }
    };
    if(!showDistrictMesh){
      setDistrictVisibility();
      return;
    }
    if(districtMeshLoaded.current){
      setDistrictVisibility();
      return;
    }
    districtMeshLoaded.current=true;
    fetch('/api/geometry/districts')
      .then(res=>{if(!res.ok)throw new Error(`Malha distrital: ${res.status}`);return res.json();})
      .then(geojson=>{
        (m.getSource('districts') as GeoJSONSource)?.setData(geojson);
        setDistrictVisibility();
      })
      .catch(()=>{districtMeshLoaded.current=false;});
  },[showDistrictMesh,styleReady]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady||!p.data)return;
    updatePoleSource(m,p);

    const poleById=new Map(p.data.poles.map(pole=>[pole.id,pole]));
    const colorByPoleId=new Map(p.data.poles.map(pole=>[pole.id,colorForTerritory(pole)]));
    const selectedTerritoryColor=p.selectedPole?colorByPoleId.get(p.selectedPole)||'#38bdf8':'#38bdf8';

    const base=p.data.territories.features.length?p.data.territories:municipalTerritories();
    const features=base.features.map((feature:any,index)=>{
      const properties={...(feature.properties||{})};
      const id=String(properties.DEMAND_ID||properties._unitId||'');
      const code=String(properties.id||properties.CD_MUN||properties.COD_IBGE||'');
      const poleIdFromFeature=String(properties.GERENCIA_ID||properties._poleId||'');
      // Nunca resolver só por município: o mesmo CD_MUN pode pertencer a vários polos.
      let unit=id?p.data!.units.find(x=>x.id===id):undefined;
      if(!unit&&poleIdFromFeature&&code){
        unit=p.data!.units.find(x=>x.municipalityCode===code&&x.poleId===poleIdFromFeature);
      }
      if(!unit&&id)unit=p.data!.units[index];
      const poleId=unit?.poleId||poleIdFromFeature||null;
      const selected=!!(unit&&p.selectedUnits.has(unit.id));
      const color=selected&&p.editable?PENDING_ASSIGN_COLOR:poleId&&colorByPoleId.has(poleId)?colorByPoleId.get(poleId)!:'#8391a7';
      return {...feature,properties:{...properties,_unitId:unit?.id||id,_poleId:poleId,_color:color,_stitch:shadeColor(selected&&p.editable?PENDING_ASSIGN_STROKE:color),_visible:p.showAll||poleId===p.selectedPole||selected,_selected:selected}};
    });
    (m.getSource('territories') as GeoJSONSource)?.setData({type:'FeatureCollection',features} as any);

    const colors=new Map<string,string>();
    const portfolioCodes:string[]=[];
    const visibleCodes:string[]=[];
    const pendingCodes:string[]=[];
    const addVisible=(code:string)=>{if(code&&code!=='0000000')visibleCodes.push(code);};

    // showAll: uma cor por município (primeiro polo estável ganha em sobreposição)
    if(p.showAll){
      const sorted=[...p.data.units].sort((a,b)=>String(a.poleId||'').localeCompare(String(b.poleId||'')));
      for(const unit of sorted){
        if(!unit.poleId||unit.type==='DISTRITO')continue;
        if(p.selectedArea){
          const pole=poleById.get(unit.poleId);
          if(!pole||pole.area!==p.selectedArea)continue;
        }
        const code=munCode(unit.municipalityCode);
        if(!colors.has(code))colors.set(code,colorByPoleId.get(unit.poleId)||'#8391a7');
        addVisible(code);
      }
    }

    // Polo selecionado: TODA a carteira usa a mesma cor desse polo
    if(p.selectedPole){
      for(const unit of p.data.units){
        if(unit.poleId!==p.selectedPole||unit.type==='DISTRITO')continue;
        const code=munCode(unit.municipalityCode);
        colors.set(code,selectedTerritoryColor);
        portfolioCodes.push(code);
        if(!p.showAll)addVisible(code);
      }
    }

    // Pré-seleção no Builder: cor pendente até Atribuir (só municípios)
    p.data.units.forEach(unit=>{
      if(unit.type==='DISTRITO'||!p.selectedUnits.has(unit.id))return;
      const code=munCode(unit.municipalityCode);
      if(!code||code==='0000000')return;
      pendingCodes.push(code);
      colors.set(code,PENDING_ASSIGN_COLOR);
      addVisible(code);
    });

    // Overlay dos municípios da tabela EXCLUDED_MUNICIPALITIES_*
    const excludedList=p.showExcluded
      ?[...new Set((p.excludedCodes||[]).map(munCode).filter(code=>code&&code!=='0000000'))]
      :[];
    if(excludedList.length){
      for(const code of excludedList){
        // Não sobrescreve pré-seleção amarela do Builder.
        if(!pendingCodes.includes(code))colors.set(code,EXCLUDED_MUNICIPALITY_COLOR);
        addVisible(code);
      }
    }
    const uniquePending=[...new Set(pendingCodes)];
    const uniqueExcluded=[...new Set(excludedList)];
    const excludedLit=uniqueExcluded.length?uniqueExcluded:['__none__'];
    const pendingLitMun=uniquePending.length?uniquePending:['__none__'];

    const pendingDistricts:string[]=[];
    const portfolioDistricts:string[]=[];
    const districtColors=new Map<string,string>();
    const addDistrictColor=(code:string,color:string)=>{
      if(!code)return;
      districtColors.set(code,color);
    };
    if(p.showAll){
      const sorted=[...p.data.units].sort((a,b)=>String(a.poleId||'').localeCompare(String(b.poleId||'')));
      for(const unit of sorted){
        if(unit.type!=='DISTRITO'||!unit.poleId)continue;
        if(p.selectedArea){
          const pole=poleById.get(unit.poleId);
          if(!pole||pole.area!==p.selectedArea)continue;
        }
        const code=distCode(unit.districtCode);
        if(!districtColors.has(code))addDistrictColor(code,colorByPoleId.get(unit.poleId)||'#8391a7');
      }
    }
    if(p.selectedPole){
      for(const unit of p.data.units){
        if(unit.type!=='DISTRITO'||unit.poleId!==p.selectedPole)continue;
        const code=distCode(unit.districtCode);
        addDistrictColor(code,selectedTerritoryColor);
        portfolioDistricts.push(code);
      }
    }
    p.data.units.forEach(unit=>{
      if(unit.type!=='DISTRITO'||!p.selectedUnits.has(unit.id))return;
      const code=distCode(unit.districtCode);
      if(!code)return;
      pendingDistricts.push(code);
      addDistrictColor(code,PENDING_ASSIGN_COLOR);
    });
    const uniquePendingDistricts=[...new Set(pendingDistricts)];
    const uniquePortfolioDistricts=[...new Set(portfolioDistricts)];

    const colorPairs:any[]=[...colors].flatMap(([code,color])=>[code,color]);
    const populationPairs:any[]=[];
    POPULATION_BANDS.forEach((band,index)=>{
      const codes=Object.entries(p.population).filter(([,value])=>populationBandIndex(value)===index).map(([code])=>munCode(code));
      if(codes.length)populationPairs.push(codes,band.color);
    });
    const populationColorExpr=p.showPopulation?['match',codeExpression,...populationPairs,'#152338'] as any:'#152338';
    const municipalityFillColor=uniqueExcluded.length
      ?(['case',['in',codeExpression,['literal',excludedLit]],EXCLUDED_MUNICIPALITY_COLOR,populationColorExpr] as any)
      :populationColorExpr;
    const municipalityFillOpacity=uniqueExcluded.length||p.showPopulation
      ?(['case',
        ['in',codeExpression,['literal',excludedLit]],.88,
        p.showPopulation?.78:(showMesh?.1:0),
      ] as any)
      :(showMesh?.1:0);
    m.setPaintProperty('municipality-fill','fill-color',municipalityFillColor);
    m.setPaintProperty('municipality-fill','fill-opacity',municipalityFillOpacity as any);
    m.setPaintProperty('portfolio-fill','fill-color',(colorPairs.length?['match',codeExpression,...colorPairs,selectedTerritoryColor]:selectedTerritoryColor) as any);
    m.setPaintProperty('portfolio-fill','fill-opacity',['case',
      ['in',codeExpression,['literal',pendingLitMun]],.52,
      ['in',codeExpression,['literal',excludedLit]],uniqueExcluded.length?.78:0,
      ['in',codeExpression,['literal',[...new Set(portfolioCodes)]]],.28,
      ['in',codeExpression,['literal',[...new Set(visibleCodes)]]],p.showPopulation?.12:.22,
      0,
    ] as any);
    const highlightCodes=[...new Set([...(p.selectedPole?portfolioCodes:visibleCodes),...uniquePending,...uniqueExcluded])];
    const highlightLit=highlightCodes.length?highlightCodes:['__none__'];
    const outlineColor=(colorPairs.length?['match',codeExpression,...colorPairs,selectedTerritoryColor]:selectedTerritoryColor) as any;
    const stitchPairs:any[]=[...colors].flatMap(([code,color])=>{
      if(color===PENDING_ASSIGN_COLOR)return [code,PENDING_ASSIGN_STROKE];
      if(color===EXCLUDED_MUNICIPALITY_COLOR)return [code,EXCLUDED_MUNICIPALITY_STROKE];
      return [code,shadeColor(color)];
    });
    const stitchColor=(stitchPairs.length?['match',codeExpression,...stitchPairs,shadeColor(selectedTerritoryColor)]:shadeColor(selectedTerritoryColor)) as any;
    if(m.getLayer('portfolio-outline')){
      m.setPaintProperty('portfolio-outline','line-color',outlineColor);
      m.setPaintProperty('portfolio-outline','line-opacity',['case',['in',codeExpression,['literal',highlightLit]],['case',
        ['in',codeExpression,['literal',pendingLitMun]],.95,
        ['in',codeExpression,['literal',excludedLit]],uniqueExcluded.length?.9:.55,
        .55,
      ],0] as any);
      m.setPaintProperty('portfolio-outline','line-width',['case',
        ['in',codeExpression,['literal',pendingLitMun]],['interpolate',['linear'],['zoom'],3,2.4,8,3.6],
        ['in',codeExpression,['literal',excludedLit]],['interpolate',['linear'],['zoom'],3,2,8,3.1],
        ['interpolate',['linear'],['zoom'],3,1.6,8,2.6],
      ] as any);
    }
    if(m.getLayer('portfolio-stitch')){
      m.setPaintProperty('portfolio-stitch','line-color',stitchColor);
      m.setPaintProperty('portfolio-stitch','line-opacity',['case',['in',codeExpression,['literal',highlightLit]],.95,0] as any);
      m.setPaintProperty('portfolio-stitch','line-dasharray',[2.4,2.2]);
    }
    m.setPaintProperty('municipality-line','line-color',['case',
      ['in',codeExpression,['literal',pendingLitMun]],PENDING_ASSIGN_STROKE,
      ['in',codeExpression,['literal',excludedLit]],EXCLUDED_MUNICIPALITY_STROKE,
      ['in',codeExpression,['literal',[...new Set(portfolioCodes)]]],selectedTerritoryColor,
      '#8391a7',
    ] as any);
    m.setPaintProperty('municipality-line','line-width',['case',
      ['in',codeExpression,['literal',pendingLitMun]],2.6,
      ['in',codeExpression,['literal',excludedLit]],2.2,
      ['in',codeExpression,['literal',[...new Set(portfolioCodes)]]],1.7,
      ['interpolate',['linear'],['zoom'],3,.15,8,.8],
    ] as any);
    if(m.getLayer('territory-fill')){
      m.setPaintProperty('territory-fill','fill-color',['case',['boolean',['get','_selected'],false],PENDING_ASSIGN_COLOR,['coalesce',['get','_color'],'#58a6ff']] as any);
      m.setPaintProperty('territory-fill','fill-opacity',['case',['boolean',['get','_selected'],false],.52,['boolean',['get','_visible'],false],.28,.02] as any);
    }
    if(m.getLayer('territory-outline')){
      m.setPaintProperty('territory-outline','line-color',['case',['boolean',['get','_selected'],false],PENDING_ASSIGN_STROKE,['coalesce',['get','_color'],'#58a6ff']] as any);
      m.setPaintProperty('territory-outline','line-width',['case',['boolean',['get','_selected'],false],['interpolate',['linear'],['zoom'],3,2.4,8,3.6],['boolean',['get','_visible'],false],['interpolate',['linear'],['zoom'],3,1.6,8,2.6],0] as any);
      m.setPaintProperty('territory-outline','line-opacity',['case',['boolean',['get','_selected'],false],.95,['boolean',['get','_visible'],false],.7,0] as any);
    }

    if(m.getLayer('district-fill')){
      const pendingLit=uniquePendingDistricts.length?uniquePendingDistricts:['__none__'];
      const portfolioLit=uniquePortfolioDistricts.length?uniquePortfolioDistricts:['__none__'];
      if(showDistrictMesh&&p.showPopulation){
        const heatStep:any=['step',['to-number',['coalesce',['get','POP_2022'],0]],POPULATION_BANDS[0].color];
        for(let i=0;i<POPULATION_BANDS.length-1;i++)heatStep.push(POPULATION_BANDS[i].max,POPULATION_BANDS[i+1].color);
        m.setPaintProperty('district-fill','fill-color',heatStep);
        m.setPaintProperty('district-fill','fill-opacity',['case',
          ['in',districtCodeExpression,['literal',pendingLit]],.92,
          .84,
        ] as any);
      }else if(showDistrictMesh){
        const districtPairs:any[]=[...districtColors].flatMap(([code,color])=>[code,color]);
        m.setPaintProperty('district-fill','fill-color',(districtPairs.length?['match',districtCodeExpression,...districtPairs,'#f59e0b']:'#f59e0b') as any);
        m.setPaintProperty('district-fill','fill-opacity',['case',
          ['in',districtCodeExpression,['literal',pendingLit]],.55,
          ['in',districtCodeExpression,['literal',portfolioLit]],.38,
          0.16,
        ] as any);
      }
    }
    if(m.getLayer('district-line')&&showDistrictMesh){
      const pendingLit=uniquePendingDistricts.length?uniquePendingDistricts:['__none__'];
      const portfolioLit=uniquePortfolioDistricts.length?uniquePortfolioDistricts:['__none__'];
      m.setPaintProperty('district-line','line-color',['case',
        ['in',districtCodeExpression,['literal',pendingLit]],PENDING_ASSIGN_STROKE,
        ['in',districtCodeExpression,['literal',portfolioLit]],selectedTerritoryColor,
        '#fbbf24',
      ] as any);
    }
  },[p.data,p.selectedPole,p.selectedUnits,p.selectedArea,p.showAll,p.showPopulation,p.population,p.showExcluded,p.excludedCodes,p.showRegionals,p.regionals,p.showPoles,p.editable,styleReady,poles,meshEpoch,showDistrictMesh,showMesh]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady)return;
    const source=m.getSource('radius-circle') as GeoJSONSource|undefined;
    if(!source)return;
    if(!p.editable||!p.selectedPole||!p.data||p.radiusKm<=0){
      source.setData({type:'FeatureCollection',features:[]});
      return;
    }
    const pole=p.data.poles.find(item=>item.id===p.selectedPole);
    if(!pole){
      source.setData({type:'FeatureCollection',features:[]});
      return;
    }
    source.setData({type:'FeatureCollection',features:[circlePolygon(pole.longitude,pole.latitude,p.radiusKm)]});
  },[p.editable,p.selectedPole,p.radiusKm,p.data,styleReady]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady)return;
    const areas=[...new Set([...(p.data?.poles||[]),...p.comparisonPoles].map(pole=>pole.area))];
    const visibleCurrent=p.showComparisonPoles?p.comparisonPoles.filter(pole=>!p.selectedArea||pole.area===p.selectedArea):[];
    (m.getSource('current-poles') as GeoJSONSource|undefined)?.setData({
      type:'FeatureCollection',
      features:visibleCurrent.map(pole=>({
        type:'Feature' as const,
        properties:{id:pole.id,name:pole.name,area:pole.area,color:lightenColor(areaColor(pole.area,areas))},
        geometry:{type:'Point' as const,coordinates:[pole.longitude,pole.latitude]},
      })),
    });
    const visibleMovements=p.showComparisonPoles&&p.showMovementLines?p.movements.filter(movement=>!p.selectedArea||movement.current.area===p.selectedArea||movement.proposed.area===p.selectedArea):[];
    (m.getSource('comparison-movements') as GeoJSONSource|undefined)?.setData({
      type:'FeatureCollection',
      features:visibleMovements.map(movement=>({
        type:'Feature' as const,
        properties:{currentId:movement.current.id,proposedId:movement.proposed.id,distanceKm:movement.distanceKm,color:lightenColor(areaColor(movement.current.area,areas),.28)},
        geometry:{type:'LineString' as const,coordinates:[[movement.current.longitude,movement.current.latitude],[movement.proposed.longitude,movement.proposed.latitude]]},
      })),
    });
  },[p.data,p.comparisonPoles,p.movements,p.showComparisonPoles,p.showMovementLines,p.selectedArea,styleReady]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady)return;
    const features=p.showRegionals?p.regionals.map(regional=>({
      type:'Feature' as const,
      properties:{id:regional.id,name:regional.name},
      geometry:{type:'Point' as const,coordinates:[regional.longitude,regional.latitude]},
    })):[];
    (m.getSource('regionals') as GeoJSONSource|undefined)?.setData({type:'FeatureCollection',features});
  },[p.showRegionals,p.regionals,styleReady]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady||!p.data)return;
    const focused=p.waveUnitId?p.data.units.find(unit=>unit.id===p.waveUnitId):undefined;
    if(!focused){
      clearWave(m);
      return;
    }

    const code=munCode(focused.municipalityCode);
    const bases:any[]=[];
    const geometry=code&&code!=='0000000'?meshByCode.current.get(code):undefined;
    if(geometry)bases.push(geometry);
    // Fallback: geometria das unidades no GeoJSON de territórios
    if(!bases.length&&p.data.territories.features.length){
      const feature=p.data.territories.features.find((item:any)=>{
        const props=item.properties||{};
        return String(props.DEMAND_ID||props._unitId||'')===focused.id
          || munCode(props.CD_MUN||props.id||props.COD_IBGE)===code;
      });
      if(feature?.geometry)bases.push(feature.geometry);
    }

    const pole=p.data.poles.find(item=>item.id===p.selectedPole);
    waveColor.current=pole?colorForTerritory(pole):'#39d98a';
    waveBases.current=bases;
    if(bases.length)startWave(m);
    else clearWave(m);

    // No Builder, clique na carteira não deve mover o mapa.
    if(!p.editable){
      m.easeTo({center:[focused.longitude,focused.latitude],zoom:Math.max(m.getZoom(),8.2),duration:650});
    }
  },[selectedKey,p.data,p.selectedPole,p.editable,styleReady,meshEpoch,poles]);

  useEffect(()=>{
    const m=map.current;
    if(!m||!styleReady||!p.selectedArea||!p.data)return;
    const poles=p.data.poles.filter(pole=>pole.area===p.selectedArea);
    if(!poles.length)return;
    const bounds=poles.reduce((box,pole)=>box.extend([pole.longitude,pole.latitude] as [number,number]),new mapboxgl.LngLatBounds());
    m.fitBounds(bounds,{padding:70,maxZoom:6.5,duration:700});
  },[p.selectedArea,p.data,styleReady]);

  return <>
    <div ref={container} className="map"/>
    <div className="map-tools">
      <button
        type="button"
        className={`map-tool${useStandard?' active':''}`}
        aria-pressed={useStandard}
        title={useStandard?'Estilo padrão do app':'Estilo Mapbox Standard'}
        aria-label={useStandard?'Estilo padrão do app':'Estilo Mapbox Standard'}
        onClick={()=>setUseStandard(v=>!v)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 7.5 12 4l8 3.5v9L12 20l-8-3.5v-9z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/>
          <path d="M12 4v16M4 7.5l8 3.5 8-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/>
        </svg>
      </button>
      <button
        type="button"
        className={`map-tool map-tool-district${showDistrictMesh?' active':''}`}
        aria-pressed={showDistrictMesh}
        title={showDistrictMesh?'Ocultar malha distrital (≥300 mil)':'Mostrar malha distrital (≥300 mil)'}
        aria-label={showDistrictMesh?'Ocultar malha distrital':'Mostrar malha distrital'}
        onClick={()=>setShowDistrictMesh(v=>!v)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 4h16v16H4V4z" fill="none" stroke="currentColor" strokeWidth="1.8"/>
          <path d="M4 12h16M12 4v16M8 4v8M16 12v8M4 8h8M12 16h8" fill="none" stroke="currentColor" strokeWidth="1.5"/>
        </svg>
      </button>
      <button
        type="button"
        className={`map-tool${showMesh?' active':''}`}
        aria-pressed={showMesh}
        title={showMesh?'Ocultar malha municipal':'Mostrar malha municipal'}
        aria-label={showMesh?'Ocultar malha municipal':'Mostrar malha municipal'}
        onClick={()=>setShowMesh(v=>!v)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 4h6v6H4V4zm10 0h6v6h-6V4zM4 14h6v6H4v-6zm10 0h6v6h-6v-6z" fill="none" stroke="currentColor" strokeWidth="1.8"/>
        </svg>
      </button>
    </div>
  </>;
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

function districtCard({name,population}:{name:string;population:number}){
  const card=document.createElement('article');
  card.className='municipality-card district-card';
  const eyebrow=document.createElement('small');
  eyebrow.textContent='DISTRITO';
  const title=document.createElement('h3');
  title.textContent=name;
  const metrics=document.createElement('div');
  metrics.className='municipality-card-metrics';
  const metric=document.createElement('span');
  const caption=document.createElement('small');caption.textContent='População';
  const amount=document.createElement('b');amount.textContent=new Intl.NumberFormat('pt-BR').format(population);
  metric.append(caption,amount);
  metrics.append(metric);
  card.append(eyebrow,title,metrics);
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

function currentPoleCard(pole:ScenarioData['poles'][number]){
  const card=document.createElement('article');
  card.className='municipality-card';
  const eyebrow=document.createElement('small');
  eyebrow.textContent='POLO NA VISÃO ATUAL';
  const title=document.createElement('h3');
  title.textContent=pole.name;
  const detail=document.createElement('p');
  detail.textContent=`${pole.area}${pole.uf?` · ${pole.uf}`:''}`;
  card.append(eyebrow,title,detail);
  return card;
}
