import path from 'path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Deliberately separate from vite.config.ts, which throws if PORT/BASE_PATH
// env vars are unset -- those are dev-server-only requirements that a test
// run has no reason to need.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
    dedupe: ['react', 'react-dom'],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: [path.resolve(import.meta.dirname, 'src/test/setup.ts')],
  },
});
