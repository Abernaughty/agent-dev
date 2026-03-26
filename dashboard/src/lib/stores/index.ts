/**
 * Store barrel export + initialisation helper.
 *
 * Issue #37: Store definitions
 * Issue #38: initAllStores() + destroyAllStores() + mock data support
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
import { connection } from './connection.svelte.js';
import { PUBLIC_USE_MOCK_DATA } from '$env/static/public';

/**
 * Kick off initial data fetch for all stores.
 *
 * In mock mode (PUBLIC_USE_MOCK_DATA=true), loads hardcoded demo data
 * and skips backend calls entirely. In live mode, fetches from the
 * proxy routes and starts PR polling.
 *
 * Called once from +layout.svelte on mount.
 */
export async function initAllStores(): Promise<void> {
	if (PUBLIC_USE_MOCK_DATA === 'true') {
		const { MOCK_AGENTS, MOCK_TASKS, MOCK_MEMORY, MOCK_PRS } = await import('./mock-data.js');
		agentsStore.loadMock(MOCK_AGENTS);
		tasksStore.loadMock(MOCK_TASKS);
		memoryStore.loadMock(MOCK_MEMORY);
		prsStore.loadMock(MOCK_PRS);
		connection.setConnected(); // Fake connected in mock mode
		return;
	}

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
	prsStore.reset(); // also stops polling
}
