import { countMunicipalitiesBySize } from '../shared/population';

const fmt=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:0});

export default function MunicipalitySizeBreakdown({populations}:{populations:number[]}){
  const bands=countMunicipalitiesBySize(populations);
  const total=populations.length;
  return (
    <div className="municipality-size-breakdown" role="region" aria-label="Municípios por faixa de população">
      <small>Por faixa de habitantes</small>
      {bands.map(band=>{
        const share=total?(band.count/total)*100:0;
        return (
          <div key={band.label}>
            <i style={{background:band.color}}/>
            <span>{band.label}</span>
            <b>{fmt.format(band.count)}</b>
            <em>{total?`${Math.round(share)}%`:'—'}</em>
          </div>
        );
      })}
    </div>
  );
}
