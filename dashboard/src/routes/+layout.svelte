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

	let { children } = $props();

	type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

	let activePanel: PanelId | null = $state('agents');
	let terminalHeight = $state(170);

	// Live badge counts from stores (#38)
	const pendingMemory = $derived(memoryStore.pendingCount);
	const pendingPR = $derived(prsStore.openCount);

	const isMockMode = import.meta.env.PUBLIC_USE_MOCK_DATA === 'true';

	onMount(() => {
		// Bootstrap: load stores (mock or live) + start SSE if live
		initAllStores();
		if (!isMockMode) {
			sseClient.connect();
		}

		return () => {
			// Teardown on layout unmount
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
	<!-- Main area: Activity bar + Sidebar + Content + Terminal -->
	<div class="flex flex-1 overflow-hidden">
		<ActivityBar
			{activePanel}
			onSelect={(panel) => (activePanel = panel)}
			{pendingMemory}
			{pendingPR}
		/>

		<SidebarPanel {activePanel} />

		<!-- Content + Bottom panel -->
		<div class="flex min-w-0 flex-1 flex-col">
			<!-- Connection status banner -->
			<ConnectionBanner />

			<!-- Main content area -->
			<div class="flex-1 overflow-y-auto">
				{@render children()}
			</div>

			<!-- Resizable terminal -->
			<BottomPanel
				height={terminalHeight}
				onResize={(h) => (terminalHeight = h)}
			/>
		</div>
	</div>

	<!-- Status bar -->
	<StatusBar />
</div>
