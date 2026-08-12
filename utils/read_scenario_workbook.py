"""Lê somente as abas pequenas necessárias à API e devolve JSON em stdout."""
from __future__ import annotations
import json, sys
from datetime import date, datetime
from pathlib import Path
import pandas as pd

SHEETS=("cenario","gerencias_propostas")

def clean(value):
    if pd.isna(value): return None
    if isinstance(value,(datetime,date)): return value.isoformat()
    if hasattr(value,"item"): value=value.item()
    return value

def main():
    path=Path(sys.argv[1]).resolve()
    if not path.is_file(): raise FileNotFoundError(path)
    book=pd.ExcelFile(path)
    result={}
    for name in SHEETS:
        if name not in book.sheet_names: result[name]=[]; continue
        frame=pd.read_excel(book,sheet_name=name)
        frame.columns=[str(c).strip().upper() for c in frame.columns]
        result[name]=[{str(k):clean(v) for k,v in row.items()} for row in frame.to_dict("records")]
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__": main()
