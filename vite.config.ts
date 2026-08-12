import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  // Vite/esbuild compila TSX sem Babel. Isso mantém compatibilidade com o
  // Nexus corporativo, que não espelha update-browserslist-db.
  plugins: [tailwindcss()],
  server: { host: '0.0.0.0', port: 5173, proxy: { '/api': 'http://127.0.0.1:3333' } },
  build: { outDir: 'dist' }
});
