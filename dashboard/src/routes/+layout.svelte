<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import ActivityBar from '$lib/components/ActivityBar.svelte';
	import SidebarPanel from '$lib/components/SidebarPanel.svelte';
	import BottomPanel from '$lib/components/BottomPanel.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import ConnectionBanner from '$lib/components/ConnectionBanner.svelte';
	import MainContent from '$lib/components/MainContent.svelte';
	import { sseClient } from '$lib/sse.js';
	import { initAllStores, destroyAllStores, memoryStore, prsStore } from '$lib/stores/index.js';

	type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

	let activePanel: PanelId | null = $state('agents');
	let terminalHeight = $state(170);

	// Live badge counts from stores (#38)
	const pendingMemory = $derived(memoryStore.pendingCount);
	const pendingPR = $derived(prsStore.openCount);

	// Selection state per panel (#38 PR3)
	const defaultSelections: Record<PanelId, string> = {
		agents: '__timeline',
		memory: '__memory-home',
		prs: '__pr-home',
		chat: '__chat'
	};

	let selections = $state<Record<PanelId, string>>({ ...defaultSelections });

	const currentPanel: PanelId = $derived(activePanel ?? 'agents');
	const selectedId: string = $derived(selections[currentPanel] ?? defaultSelections[currentPanel]);

	function handleSelect(id: string) {
		selections = { ...selections, [currentPanel]: id };
	}

	function handlePanelSwitch(panel: PanelId | null) {
		activePanel = panel;
	}

	const isMockMode = import.meta.env.PUBLIC_USE_MOCK_DATA === 'true';

	onMount(() => {
		initAllStores();
		if (!isMockMode) {
			sseClient.connect();
		}

		return () => {
			if (!isMockMode) {
				sseClient.disconnect();
			}
			destroyAllStores();
		};
	});
</script>

<svelte:head>
	<title>Agent Workforce Dashboard</title>
</svelte:head>

<div class="flex h-screen flex-col overflow-hidden" style="background: var(--color-bg-primary);">
	<div class="flex flex-1 overflow-hidden">
		<ActivityBar
			{activePanel}
			onSelect={handlePanelSwitch}
			{pendingMemory}
			{pendingPR}
		/>

		<SidebarPanel
			{activePanel}
			{selectedId}
			onSelect={handleSelect}
		/>

		<div class="flex min-w-0 flex-1 flex-col">
			<ConnectionBanner />

			<div class="flex-1 overflow-y-auto">
				<MainContent {activePanel} {selectedId} />
			</div>

			<BottomPanel
				height={terminalHeight}
				onResize={(h) => (terminalHeight = h)}
			/>
		</div>
	</div>

	<StatusBar />
</div>
