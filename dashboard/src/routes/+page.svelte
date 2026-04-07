<!--
	Home route — renders MainContent using dashboard context from the layout.

	The layout owns the UI state (activePanel, selectedId) via setDashboardContext().
	This page reads it via getDashboardContext() and passes it to MainContent.

	Issue #38: Data Integration
	Fix: Use $derived to maintain reactivity when passing context getters as props.
	Without this, Svelte 5 can lose track of reactive dependencies when getter
	properties on plain objects are accessed in template expressions.
-->
<script lang="ts">
	import MainContent from '$lib/components/MainContent.svelte';
	import { getDashboardContext } from '$lib/stores/dashboard.svelte.js';

	const dash = getDashboardContext();

	// Wrap context getters in $derived to guarantee Svelte tracks them reactively.
	// Without this, switching sidebar selections can silently fail to update MainContent.
	const activePanel = $derived(dash.activePanel);
	const selectedId = $derived(dash.selectedId);
</script>

<MainContent {activePanel} {selectedId} />
