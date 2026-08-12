import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { MAPBOX_ACCESS_TOKEN, ROOT } from './config.js';

describe('runtime config', () => {
  it('provides a public Mapbox token without requiring .env', () => {
    assert.match(MAPBOX_ACCESS_TOKEN, /^pk\./);
  });

  it('resolves the project root independently of the working directory', () => {
    assert.match(ROOT.replaceAll('\\', '/'), /Reestruturacao_Equipe$/);
  });
});
