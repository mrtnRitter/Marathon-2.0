import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  publicDir: path.join(root, 'public'),
  build: {
    outDir: path.resolve(root, '../marathon/server/static'),
    emptyOutDir: true,
  },
});

// build.py places ui/public/fonts/JetBrainsMono-Regular.woff2 here.
