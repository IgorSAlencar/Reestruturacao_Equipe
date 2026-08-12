import sql from 'mssql';

const asBoolean = (value, fallback) => {
  if (value === undefined || value === '') return fallback;
  return String(value).toLowerCase() === 'true';
};

export function getSqlConfig(env = process.env) {
  const user = env.SQL_USER || env.SQL_USERNAME || '';
  const password = env.SQL_PASSWORD || '';

  if (!user || !password) {
    throw new Error('Defina SQL_USER e SQL_PASSWORD no arquivo .env.');
  }

  return {
    server: env.SQL_SERVER || 'MZ-VV-BD-182',
    database: env.SQL_DATABASE || 'TESTE',
    user,
    password,
    domain: env.SQL_DOMAIN || 'CORP',
    options: {
      instanceName: env.SQL_INSTANCE || 'MSSQL2008A',
      trustedConnection: asBoolean(env.SQL_TRUSTED_CONNECTION, true),
      encrypt: asBoolean(env.SQL_ENCRYPT, false),
      trustServerCertificate: asBoolean(env.SQL_TRUST_SERVER_CERTIFICATE, true),
      enableArithAbort: true,
    },
    pool: {
      max: Number(env.SQL_POOL_MAX || 10),
      min: 0,
      idleTimeoutMillis: Number(env.SQL_IDLE_TIMEOUT_MS || 30_000),
    },
    connectionTimeout: Number(env.SQL_CONNECTION_TIMEOUT_MS || 30_000),
    requestTimeout: Number(env.SQL_REQUEST_TIMEOUT_MS || 120_000),
  };
}

let poolPromise;

export async function getSqlPool() {
  if (!poolPromise) {
    const candidate = new sql.ConnectionPool(getSqlConfig());
    candidate.on('error', (error) => console.error('Erro no pool SQL Server:', error.message));
    poolPromise = candidate.connect().then(() => candidate).catch((error) => {
      poolPromise = undefined;
      throw error;
    });
  }
  return poolPromise;
}

export async function testSqlConnection() {
  const connection = await getSqlPool();
  const result = await connection.request().query(`
    SELECT
      DB_NAME() AS databaseName,
      @@SERVERNAME AS serverName,
      SYSTEM_USER AS authenticatedUser
  `);
  return result.recordset[0];
}

export async function closeSqlPool() {
  if (!poolPromise) return;
  const activePool = poolPromise;
  poolPromise = undefined;
  const connection = await activePool.catch(() => null);
  if (connection) await connection.close();
}

export { sql };
