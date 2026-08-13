"""Lê somente as abas pequenas necessárias à API e devolve JSON em stdout."""
from __future__ import annotations
import argparse, json
from datetime import date, datetime
from pathlib import Path
import pandas as pd

SUMMARY_SHEETS=("cenario","gerencias_propostas")
UNIT_COLUMNS={
    "DEMAND_ID", "GERENCIA_ID", "TIPO_UNIDADE", "COD_IBGE", "CD_MUN",
    "CD_DIST", "NM_MUN", "NM_DIST", "UF", "POPULACAO_UNIDADE",
    "QTD_LOJAS", "LATITUDE", "LONGITUDE", "DISTANCIA_KM",
}

def clean(value):
    if pd.isna(value): return None
    if isinstance(value,(datetime,date)): return value.isoformat()
    if hasattr(value,"item"): value=value.item()
    return value

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--include-units",action="store_true")
    args=parser.parse_args()
    path=Path(args.path).resolve()
    if not path.is_file(): raise FileNotFoundError(path)
    book=pd.ExcelFile(path)
    result={}
    sheets=(*SUMMARY_SHEETS, *(("unidades_atendidas",) if args.include_units else ()))
    for name in sheets:
        if name not in book.sheet_names: result[name]=[]; continue
        frame=pd.read_excel(book,sheet_name=name)
        frame.columns=[str(c).strip().upper() for c in frame.columns]
        if name=="unidades_atendidas":
            frame=frame[[column for column in frame.columns if column in UNIT_COLUMNS]]
        result[name]=[{str(k):clean(v) for k,v in row.items()} for row in frame.to_dict("records")]
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__": main()
