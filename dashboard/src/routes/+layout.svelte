<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import ActivityBar from '$lib/components/ActivityBar.svelte';
	import SidebarPanel from '$lib/components/SidebarPanel.svelte';
	import BottomPanel from '$lib/components/BottomPanel.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import ConnectionBanner from '$lib/components/ConnectionBanner.svelte';
	import { sseClient } from '$lib/sse.js';
	import { initAllStores, destroyAllStores, memoryStore, prsStore } from '$lib/stores/index.js';
	import { setDashboardContext } from '$lib/stores/dashboard.svelte.js';
	import { PUBLIC_USE_MOCK_DATA } from '$env/static/public';

	let { children } = $props();

	// Create dashboard UI context (activePanel, selectedId, handlers)
	const dash = setDashboardContext();

	let terminalHeight = $state(170);

	// Live badge counts from stores (#38)
	const pendingMemory = $derived(memoryStore.pendingCount);
	const pendingPR = $derived(prsStore.openCount);

	const isMockMode = PUBLIC_USE_MOCK_DATA === 'true';

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
			activePanel={dash.activePanel}
			onSelect={dash.handlePanelSwitch}
			{pendingMemory}
			{pendingPR}
		/>

		<SidebarPanel
			activePanel={dash.activePanel}
			selectedId={dash.selectedId}
			onSelect={dash.handleSelect}
		/>

		<div class="flex min-w-0 flex-1 flex-col">
			<ConnectionBanner />

			<div class="flex-1 overflow-y-auto">
				{@render children()}
			</div>

			<BottomPanel
				height={terminalHeight}
				onResize={(h) => (terminalHeight = h)}
			/>
		</div>
	</div>

	<StatusBar />
</div>
