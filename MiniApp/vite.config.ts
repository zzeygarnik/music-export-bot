import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(({mode: _mode}) => {
  return {
    base: '/player/',
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    build: {
      rollupOptions: {
        output: {
          entryFileNames: 'assets/app.[hash].js',
          chunkFileNames: 'assets/[name].[hash].js',
          assetFileNames: 'assets/app.[hash].[ext]',
        },
      },
    },
    server: {
      hmr: process.env.DISABLE_HMR !== 'true',
    },
  };
});
