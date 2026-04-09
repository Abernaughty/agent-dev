<script>
  import { onMount, onDestroy } from 'svelte';

  /** The current height of the bottom panel in pixels */
  export let height = 200;

  /** Whether the panel is open/visible */
  export let open = true;

  /** Minimum allowed panel height in pixels */
  const MIN_HEIGHT = 60;

  /** Whether a drag-resize operation is currently in progress */
  let dragging = false;

  /** The Y coordinate where the drag started */
  let startY = 0;

  /** The panel height when the drag started */
  let startHeight = 0;

  /**
   * Named mousemove handler for drag-to-resize.
   * Computes the new panel height based on cursor movement and clamps it
   * between MIN_HEIGHT (60px) and 80% of the viewport height.
   * @param {MouseEvent} e
   */
  function handleMouseMove(e) {
    if (!dragging) return;
    const delta = startY - e.clientY;
    const newHeight = startHeight + delta;
    height = Math.min(window.innerHeight * 0.8, Math.max(MIN_HEIGHT, newHeight));
  }

  /**
   * Named mouseup handler that ends the drag-resize operation.
   * Removes both mousemove and mouseup listeners from window and resets
   * the dragging state flag.
   */
  function handleMouseUp() {
    dragging = false;
    window.removeEventListener('mousemove', handleMouseMove);
    window.removeEventListener('mouseup', handleMouseUp);
  }

  /**
   * Mousedown handler on the resize handle that initiates the drag.
   * Records the starting cursor position and panel height, sets the dragging
   * flag, and attaches mousemove/mouseup listeners to window for reliable
   * capture during fast drags.
   * @param {MouseEvent} e
   */
  function handleMouseDown(e) {
    e.preventDefault();
    dragging = true;
    startY = e.clientY;
    startHeight = height;
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  }

  /**
   * Safety cleanup: remove any lingering window event listeners when the
   * component is destroyed (e.g. if the user is mid-drag when the component
   * unmounts).
   */
  onDestroy(() => {
    window.removeEventListener('mousemove', handleMouseMove);
    window.removeEventListener('mouseup', handleMouseUp);
    dragging = false;
  });
</script>

{#if open}
  <div class="bottom-panel" style="height: {height}px;">
    <!-- Resize handle at the top edge of the panel -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div
      class="resize-handle"
      on:mousedown={handleMouseDown}
    ></div>

    <div class="bottom-panel-content">
      <slot />
    </div>
  </div>
{/if}

<style>
  .bottom-panel {
    position: relative;
    width: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    border-top: 1px solid var(--border-color, #e0e0e0);
    background: var(--panel-bg, #1e1e1e);
  }

  .resize-handle {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 4px;
    cursor: ns-resize;
    z-index: 10;
  }

  .resize-handle:hover {
    background: var(--resize-handle-hover, rgba(100, 100, 255, 0.4));
  }

  .bottom-panel-content {
    flex: 1;
    overflow: auto;
    padding-top: 4px;
  }
</style>
