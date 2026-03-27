/**
 * Store barrel export + initialisation helper.
 *
 * Issue #37: Store definitions
 * Issue #38: initAllStores() + destroyAllStores()
 * Issue #51: Removed mock mode — always live
 */

export { agentsStore } from './agents.svelte.js';
export { tasksStore } from './tasks.svelte.js';
export { memoryStore } from './memory.svelte.js';
export { prsStore } from './prs.svelte.js';
export { connection } from './connection.svelte.js';

import { agentsStore } from './agents.svelte.js';
import { tasksStore } from './tasks.svelte.js';
import { memoryStore } from './memory.svelte.js';
import { prsStore } from './prs.svelte.js';

/**
 * Kick off initial data fetch for all stores.
 *
 * Fetches from the proxy routes and starts PR polling.
 * If any backend is unavailable, stores start empty with
 * error state — graceful degradation per data source.
 *
 * Called once from +layout.svelte on mount.
 */
export async function initAllStores(): Promise<void> {
	agentsStore.refresh();
	tasksStore.refresh();
	memoryStore.refresh();
	prsStore.refresh();
	prsStore.startPolling();
}

/**
 * Tear down all stores (disconnect, reset, stop polling).
 * Called on layout unmount.
 */
export function destroyAllStores(): void {
	agentsStore.reset();
	tasksStore.reset();
	memoryStore.reset();
	prsStore.reset();
}
