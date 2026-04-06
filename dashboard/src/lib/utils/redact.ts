/**
 * Client-side secret redaction utility (Layer 4 of defense model).
 *
 * Belt-and-suspenders with server-side scanning in e2b_runner.py.
 * Patterns are ported directly from dev-suite/src/sandbox/e2b_runner.py
 * to ensure parity between Python and TypeScript redaction layers.
 *
 * Issue #107: Sandbox stdout/stderr in task timeline
 */

const SECRET_PATTERNS: RegExp[] = [
	/sk-ant-[a-zA-Z0-9_-]{20,}/g, // Anthropic
	/sk-[a-zA-Z0-9]{20,}/g, // OpenAI-style
	/AIza[a-zA-Z0-9_-]{35}/g, // Google
	/ghp_[a-zA-Z0-9]{36}/g, // GitHub PAT
	/ghs_[a-zA-Z0-9]{36}/g, // GitHub App token
	/e2b_[a-zA-Z0-9]{20,}/g, // E2B
	/npm_[a-zA-Z0-9]{36}/g, // npm token
	/(password|secret|token|key)\s*[=:]\s*\S+/gi // Generic key=value
];

/**
 * Redact known secret patterns from text before rendering.
 *
 * Returns the input with all matched patterns replaced by [REDACTED].
 * Safe to call on empty/null input — returns empty string.
 */
export function redactSecrets(text: string | null | undefined): string {
	if (!text) return '';
	let redacted = text;
	for (const pattern of SECRET_PATTERNS) {
		// Reset lastIndex for global patterns
		pattern.lastIndex = 0;
		redacted = redacted.replace(pattern, '[REDACTED]');
	}
	return redacted;
}
