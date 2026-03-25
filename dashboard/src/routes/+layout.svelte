<script lang="ts">
	import '../app.css';
	import ActivityBar from '$lib/components/ActivityBar.svelte';
	import SidebarPanel from '$lib/components/SidebarPanel.svelte';
	import BottomPanel from '$lib/components/BottomPanel.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';

	let { children } = $props();

	type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

	let activePanel: PanelId | null = $state('agents');
	let terminalHeight = $state(170);

	// Stub badge counts — will be dynamic in sub-task 2
	const pendingMemory = 3;
	const pendingPR = 1;
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
