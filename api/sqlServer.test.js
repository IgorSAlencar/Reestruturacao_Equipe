import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { getSqlConfig } from './sqlServer.js';

describe('SQL Server config', () => {
  it('matches the corporate mssql connection shape', () => {
    const config = getSqlConfig({
      SQL_SERVER: 'SERVER', SQL_DATABASE: 'DB', SQL_USER: 'user', SQL_PASSWORD: 'secret',
      SQL_DOMAIN: 'DOMAIN', SQL_INSTANCE: 'INSTANCE', SQL_TRUSTED_CONNECTION: 'true',
      SQL_ENCRYPT: 'false', SQL_TRUST_SERVER_CERTIFICATE: 'true',
    });
    assert.equal(config.server, 'SERVER');
    assert.equal(config.database, 'DB');
    assert.equal(config.user, 'user');
    assert.equal(config.domain, 'DOMAIN');
    assert.equal(config.options.instanceName, 'INSTANCE');
    assert.equal(config.options.trustedConnection, true);
    assert.equal(config.options.encrypt, false);
    assert.equal(config.options.trustServerCertificate, true);
  });

  it('enables trusted connection by default', () => {
    const config = getSqlConfig({SQL_USER: 'user', SQL_PASSWORD: 'secret'});
    assert.equal(config.options.trustedConnection, true);
  });

  it('refuses to start a connection without credentials', () => {
    assert.throws(() => getSqlConfig({}), /SQL_USER e SQL_PASSWORD/);
  });
});
