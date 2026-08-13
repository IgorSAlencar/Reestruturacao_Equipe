/** Cores sólidas por DESC_GERENCIA_AREA (markers e legenda). */
const AREA_COLORS=[
  '#39d98a','#58a6ff','#ffb454','#c792ea','#ff6b81','#4fd1c5','#f6e05e','#7f9cf5',
  '#ed64a6','#68d391','#f687b3','#63b3ed','#fbd38d','#9f7aea','#38b2ac','#fc8181',
];

/**
 * Paleta para pintar a região de atuação de cada polo.
 * Cores bem distintas para diferenciar carteiras vizinhas quando várias estão visíveis.
 */
const TERRITORY_COLORS=[
  '#e6194b','#3cb44b','#4363d8','#f58231','#911eb4','#42d4f4','#f032e6','#bfef45',
  '#fabed4','#469990','#dcbeff','#9a6324','#fffac8','#800000','#aaffc3','#808000',
  '#ffd8b1','#000075','#a9a9a9','#ffe119','#e6beff','#1abc9c','#e74c3c','#3498db',
  '#f39c12','#9b59b6','#2ecc71','#e67e22','#1abc9c','#c0392b','#2980b9','#8e44ad',
];

/** Destaque de municípios pré-selecionados no Builder (antes de Atribuir). */
export const PENDING_ASSIGN_COLOR='#f59e0b';
export const PENDING_ASSIGN_STROKE='#b45309';

/** Municípios da tabela EXCLUDED_MUNICIPALITIES_* (overlay no mapa). */
export const EXCLUDED_MUNICIPALITY_COLOR='#3b2414';
export const EXCLUDED_MUNICIPALITY_STROKE='#1f120a';

type ColoredPole={id:string;area:string};

export function areaColor(area:string,areas:string[]=[]){
  const ordered=[...new Set(areas)].sort((a,b)=>a.localeCompare(b,'pt-BR'));
  const index=ordered.indexOf(area);
  if(index>=0)return AREA_COLORS[index%AREA_COLORS.length];
  return AREA_COLORS[Math.abs([...area].reduce((n,c)=>(n*31+c.charCodeAt(0))|0,0))%AREA_COLORS.length];
}

/** Cor do marker: uma cor sólida por gerência de área. */
export function markerColor(pole:ColoredPole,poles:ColoredPole[]){
  const areas=[...new Set(poles.map(item=>item.area))];
  return areaColor(pole.area,areas);
}

/**
 * Cor da região de atuação do polo (carteira).
 * Cada polo recebe uma cor distinta da paleta, independente da gerência de área.
 */
export function territoryColor(pole:ColoredPole,poles:ColoredPole[]){
  const ordered=[...poles].sort((a,b)=>a.id.localeCompare(b.id));
  const index=Math.max(0,ordered.findIndex(item=>item.id===pole.id));
  if(index<TERRITORY_COLORS.length)return TERRITORY_COLORS[index];
  // Além da paleta: espalha no círculo de matiz (ângulo áureo) para manter distinção.
  const hue=Math.round((index*137.508)%360);
  const saturation=58+(index%4)*8;
  const lightness=42+(index%5)*5;
  return `hsl(${hue}, ${saturation}%, ${lightness}%)`;
}

/** Escurece uma cor hex/hsl para contorno estilo costura. */
export function shadeColor(color:string,factor=0.62){
  const hsl=color.trim().match(/^hsl\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)$/i);
  if(hsl){
    const lightness=Math.max(12,Math.min(92,Number(hsl[3])*factor));
    return `hsl(${hsl[1]}, ${hsl[2]}%, ${Math.round(lightness)}%)`;
  }
  const hex=color.trim().replace('#','');
  if(!/^[0-9a-f]{6}$/i.test(hex))return color;
  const value=parseInt(hex,16);
  const channel=(shift:number)=>Math.max(0,Math.min(255,Math.round(((value>>shift)&255)*factor)));
  const r=channel(16),g=channel(8),b=channel(0);
  return `#${[r,g,b].map(n=>n.toString(16).padStart(2,'0')).join('')}`;
}

/** @deprecated Use markerColor ou territoryColor conforme o contexto. */
export function poleColor(pole:ColoredPole,poles:ColoredPole[]){
  return territoryColor(pole,poles);
}

export function areaGradient(area:string,poles:ColoredPole[]){
  return areaColor(area,[...new Set(poles.map(pole=>pole.area))]);
}
