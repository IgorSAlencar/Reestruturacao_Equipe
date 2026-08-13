import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react-swc';

export default defineConfig(({mode}) => {
  const env=loadEnv(mode,process.cwd(),'');
  const appHost=process.env.APP_HOST||env.APP_HOST||'10.206.168.97';
  const apiHost=process.env.API_HOST||env.API_HOST||appHost;
  const apiPort=Number(process.env.API_PORT||env.API_PORT||333);
  const webPort=Number(process.env.WEB_PORT||env.WEB_PORT||5173);
  const proxy={'/api': `http://${apiHost}:${apiPort}`};
  return {
    plugins: [react()],
    server: {
      host: appHost,
      port: webPort,
      strictPort: true,
      watch: {ignored: ['**/.territorios-data/**']},
      proxy,
    },
    preview: {host:appHost,port:webPort,strictPort:true,proxy},
    build: {outDir:'dist'},
  };
});
