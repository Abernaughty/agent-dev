/**
 * Connection store — tracks backend connectivity.
 *
 * Drives the "Backend unreachable" banner and StatusBar indicator.
 * Updated by the SSE client on connect/disconnect/reconnect.
 *
 * Issue #37
 */

import type { ConnectionStatus } from '$lib/types/api.js';

let status = $state<ConnectionStatus>('disconnected');
let reconnectAttempt = $state(0);

export const connection = {
	get status() {
		return status;
	},
	get reconnectAttempt() {
		return reconnectAttempt;
	},
	get isConnected() {
		return status === 'connected';
	},

	setConnected() {
		status = 'connected';
		reconnectAttempt = 0;
	},
	setDisconnected() {
		status = 'disconnected';
	},
	setReconnecting(attempt: number) {
		status = 'reconnecting';
		reconnectAttempt = attempt;
	}
};
