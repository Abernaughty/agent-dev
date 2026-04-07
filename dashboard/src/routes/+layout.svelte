<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import MenuBar from '$lib/components/MenuBar.svelte';
	import ActivityBar from '$lib/components/ActivityBar.svelte';
	import SidebarPanel from '$lib/components/SidebarPanel.svelte';
	import BottomPanel from '$lib/components/BottomPanel.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import ConnectionBanner from '$lib/components/ConnectionBanner.svelte';
	import { sseClient } from '$lib/sse.js';
	import { initAllStores, destroyAllStores, memoryStore, prsStore } from '$lib/stores/index.js';
	import { setDashboardContext } from '$lib/stores/dashboard.svelte.js';

	let { children } = $props();

	const dash = setDashboardContext();

	let terminalHeight = $state(170);

	// Wrap context getters in $derived to ensure Svelte 5 tracks them reactively
	// when passed as component props. Without this, sidebar/content can go stale.
	const activePanel = $derived(dash.activePanel);
	const selectedId = $derived(dash.selectedId);
	const pendingMemory = $derived(memoryStore.pendingCount);
	const pendingPR = $derived(prsStore.openCount);

	onMount(() => {
		initAllStores();
		sseClient.connect();

		return () => {
			sseClient.disconnect();
			destroyAllStores();
		};
	});
</script>

<svelte:head>
	<title>Agent Workforce Dashboard</title>
</svelte:head>

<div class="flex h-screen flex-col overflow-hidden" style="background: var(--color-bg-primary);">
	<MenuBar />

	<div class="flex flex-1 overflow-hidden">
		<ActivityBar
			{activePanel}
			onSelect={dash.handlePanelSwitch}
			{pendingMemory}
			{pendingPR}
		/>

		<SidebarPanel
			{activePanel}
			{selectedId}
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
