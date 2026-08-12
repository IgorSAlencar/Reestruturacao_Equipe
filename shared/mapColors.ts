const AREA_COLORS=[
  '#39d98a','#58a6ff','#ffb454','#c792ea','#ff6b81','#4fd1c5','#f6e05e','#7f9cf5',
  '#ed64a6','#68d391','#f687b3','#63b3ed','#fbd38d','#9f7aea','#38b2ac','#fc8181',
];

type ColoredPole={id:string;area:string};

export function areaColor(area:string,areas:string[]=[]){
  const ordered=[...new Set(areas)].sort((a,b)=>a.localeCompare(b,'pt-BR'));
  const index=ordered.indexOf(area);
  if(index>=0)return AREA_COLORS[index%AREA_COLORS.length];
  return AREA_COLORS[Math.abs([...area].reduce((n,c)=>(n*31+c.charCodeAt(0))|0,0))%AREA_COLORS.length];
}

export function poleColor(pole:ColoredPole,poles:ColoredPole[]){
  const areas=[...new Set(poles.map(item=>item.area))];
  const base=areaColor(pole.area,areas);
  const siblings=poles.filter(item=>item.area===pole.area).sort((a,b)=>a.id.localeCompare(b.id));
  const index=Math.max(0,siblings.findIndex(item=>item.id===pole.id));
  if(index===0)return base;
  const hsl=rgbToHsl(hexToRgb(base));
  const hue=(hsl.h+(index*11)%37-18+360)%360;
  const saturation=Math.max(48,Math.min(88,hsl.s+(index*7)%21-10));
  const lightness=Math.max(32,Math.min(72,hsl.l+(index*13)%31-15));
  return `hsl(${Math.round(hue)}, ${Math.round(saturation)}%, ${Math.round(lightness)}%)`;
}

export function areaGradient(area:string,poles:ColoredPole[]){
  const colors=poles.filter(pole=>pole.area===area).sort((a,b)=>a.id.localeCompare(b.id)).map(pole=>poleColor(pole,poles));
  return colors.length>1?`linear-gradient(135deg,${colors.join(',')})`:(colors[0]||areaColor(area));
}

function hexToRgb(hex:string){
  const value=parseInt(hex.slice(1),16);
  return {r:value>>16,g:(value>>8)&255,b:value&255};
}

function rgbToHsl({r,g,b}:{r:number;g:number;b:number}){
  const red=r/255,green=g/255,blue=b/255;
  const max=Math.max(red,green,blue),min=Math.min(red,green,blue),delta=max-min;
  let h=0;
  if(delta){
    if(max===red)h=60*(((green-blue)/delta)%6);
    else if(max===green)h=60*((blue-red)/delta+2);
    else h=60*((red-green)/delta+4);
  }
  const l=(max+min)/2;
  const s=delta===0?0:delta/(1-Math.abs(2*l-1));
  return {h:(h+360)%360,s:s*100,l:l*100};
}
