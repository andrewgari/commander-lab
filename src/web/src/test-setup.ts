import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/svelte';

// vitest runs without globals, so testing-library cannot auto-register its cleanup.
afterEach(() => cleanup());
