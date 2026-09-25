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
    // The default 5000ms is occasionally too tight for a userEvent.type()-heavy
    // test under full-suite parallel load (confirmed repeatedly this session:
    // different individual tests in the same file time out on different runs,
    // never the same one twice -- a resource-contention pattern, not a slow
    // test) -- widened rather than patched test-by-test.
    testTimeout: 15000,
  },
});
