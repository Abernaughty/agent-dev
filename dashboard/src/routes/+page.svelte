<!--
	Home route — renders MainContent using dashboard context from the layout.

	The layout owns the UI state (activePanel, selectedId) via setDashboardContext().
	This page reads it via getDashboardContext() and passes it to MainContent.

	Issue #38: Data Integration
-->
<script lang="ts">
	import MainContent from '$lib/components/MainContent.svelte';
	import { getDashboardContext } from '$lib/stores/dashboard.svelte.js';

	const dash = getDashboardContext();
</script>

<MainContent activePanel={dash.activePanel} selectedId={dash.selectedId} />
