/**
 * Dashboard UI context — shared between +layout.svelte and +page.svelte.
 *
 * Holds the panel selection state (which ActivityBar panel is active,
 * which sidebar item is selected). Exposed via Svelte context so the
 * layout owns the state and child routes can read it.
 *
 * Issue #38: Data Integration
 */

import { getContext, setContext } from 'svelte';

export type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

const DASHBOARD_CTX_KEY = Symbol('dashboard');

const DEFAULT_SELECTIONS: Record<PanelId, string> = {
	agents: '__timeline',
	memory: '__memory-home',
	prs: '__pr-home',
	chat: '__chat'
};

export interface DashboardContext {
	readonly activePanel: PanelId | null;
	readonly currentPanel: PanelId;
	readonly selectedId: string;
	handleSelect: (id: string) => void;
	handlePanelSwitch: (panel: PanelId | null) => void;
}

export function setDashboardContext(): DashboardContext {
	let activePanel = $state<PanelId | null>('agents');
	let selections = $state<Record<PanelId, string>>({ ...DEFAULT_SELECTIONS });

	const currentPanel: PanelId = $derived(activePanel ?? 'agents');
	const selectedId: string = $derived(selections[currentPanel] ?? DEFAULT_SELECTIONS[currentPanel]);

	function handleSelect(id: string) {
		selections = { ...selections, [currentPanel]: id };
	}

	function handlePanelSwitch(panel: PanelId | null) {
		activePanel = panel;
	}

	const ctx: DashboardContext = {
		get activePanel() { return activePanel; },
		get currentPanel() { return currentPanel; },
		get selectedId() { return selectedId; },
		handleSelect,
		handlePanelSwitch
	};

	setContext(DASHBOARD_CTX_KEY, ctx);
	return ctx;
}

export function getDashboardContext(): DashboardContext {
	return getContext<DashboardContext>(DASHBOARD_CTX_KEY);
}
