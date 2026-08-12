import pandas as pd
from sqlalchemy import create_engine

# Dados da conexão
server = 'DESKTOP-G4V6794'
database = 'TESTE'
username = 'sa'
password = 'expresso'

# Criar a string de conexão para SQLAlchemy
conn_str = (
    f"mssql+pyodbc://{username}:{password}@{server}/{database}"
    "?driver=ODBC+Driver+17+for+SQL+Server"
    "&TrustServerCertificate=yes"
)

# Criar engine com SQLAlchemy
engine = create_engine(conn_str)

# Ler municipios.json e transformar em DataFrame
# utf-8-sig remove o BOM que o pandas rejeita
df_municipios = pd.read_json("municipios.json", encoding="utf-8-sig")
df_municipios.to_sql('MUNICIPIOS_COORDENADAS', con=engine, if_exists='replace', index=False)