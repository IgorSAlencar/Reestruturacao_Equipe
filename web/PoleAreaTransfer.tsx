type Props={
  currentArea:string;
  areas:string[];
  onChange:(area:string)=>void;
};

export default function PoleAreaTransfer({currentArea,areas,onChange}:Props){
  const options=[...new Set([currentArea,...areas].filter(Boolean))].sort((a,b)=>a.localeCompare(b,'pt-BR'));
  return (
    <div className="area-transfer">
      <label className="area-transfer-field">
        <small>Gerência de área</small>
        <select value={currentArea} aria-label="Trocar gerência de área" onChange={event=>onChange(event.target.value)}>
          {options.map(area=><option key={area} value={area}>{area}</option>)}
        </select>
      </label>
      <form
        className="area-transfer-new"
        onSubmit={event=>{
          event.preventDefault();
          const form=event.currentTarget;
          const input=form.elements.namedItem('newArea') as HTMLInputElement;
          const next=input.value;
          if(!next.trim())return;
          onChange(next);
          input.value='';
        }}
      >
        <input name="newArea" placeholder="Nova gerência…" aria-label="Nome da nova gerência de área"/>
        <button type="submit">Criar</button>
      </form>
    </div>
  );
}
