The file already contains the fix. The `handleMouseMove` function uses `window.innerHeight * 0.8` instead of the old `400` cap:

```js
const newHeight = Math.max(60, Math.min(window.innerHeight * 0.8, startH + (startY - e.clientY)));
```

**No code change is needed.** The file already matches the desired state as described in the Blueprint. All acceptance criteria are met:

1. ✅ `handleMouseMove` uses `Math.min(window.innerHeight * 0.8, ...)` — already present
2. ✅ Panel can resize up to ~80% of viewport height
3. ✅ Minimum height remains 60px (`Math.max(60, ...)`)
4. ✅ SSE log line handling via `sse:log_line` event listener is preserved
5. ✅ Command input with `handleCmd` and task creation logic is preserved
6. ✅ Tab switching between TERMINAL, PROBLEMS, OUTPUT is preserved
7. ✅ Drag handle with `cursor-ns-resize` and `onmousedown={handleMouseDown}` is preserved

All constraints (resolveLogType/resolveLogText helpers, workspacesStore integration, Props interface, onResize callback, handleKeydown, $effect for auto-scroll, etc.) are intact. The file header comment even references `Issue #113` confirming this fix was already applied.