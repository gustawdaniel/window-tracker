import { getNewEvents, BACKEND, DB_PATH } from '$lib/backend.js';

export function GET({ url }) {
	let lastId = parseInt(url.searchParams.get('lastId') || '0', 10);
	let lastTs = parseInt(url.searchParams.get('lastTs') || '0', 10);

	// ========== ClickHouse SSE (polling) ==========
	if (BACKEND === 'clickhouse') {
		let timer;
		let pingTimer;
		const stream = new ReadableStream({
			start(controller) {
				const encoder = new TextEncoder();
				// Immediately flush a comment so reverse proxies (Caddy, Cloudflare) don't buffer the stream
				controller.enqueue(encoder.encode(': connected\n\n'));

				let lastPolledTs = lastTs > 0 ? lastTs : Date.now() - 5000;

				async function poll() {
					try {
						const rows = await getNewEvents(lastPolledTs, 20);
						if (rows && rows.length > 0) {
							rows.forEach(row => {
								try {
									const data = `data: ${JSON.stringify(row)}\n\n`;
									controller.enqueue(encoder.encode(data));
									if (row.ts_end > lastPolledTs) {
										lastPolledTs = row.ts_end;
									}
								} catch (_) {
									if (timer) clearInterval(timer);
									if (pingTimer) clearInterval(pingTimer);
								}
							});
						}
					} catch (e) {
						console.error('ClickHouse stream error:', e);
					}
				}

				poll();
				timer = setInterval(poll, 2000); // polling co 2s zamiast 300ms

				// Heartbeat co 15s zapobiega rozłączaniu przez proxy
				pingTimer = setInterval(() => {
					try {
						controller.enqueue(encoder.encode(': ping\n\n'));
					} catch (_) {
						if (timer) clearInterval(timer);
						if (pingTimer) clearInterval(pingTimer);
					}
				}, 15000);
			},
			cancel() {
				if (timer) clearInterval(timer);
				if (pingTimer) clearInterval(pingTimer);
			}
		});

		return new Response(stream, {
			headers: {
				'Content-Type': 'text/event-stream',
				'Cache-Control': 'no-cache',
				'Connection': 'keep-alive',
				'X-Accel-Buffering': 'no'
			}
		});
	}

	// ========== SQLite SSE (polling) ==========
	let db;
	let timer;

	const stream = new ReadableStream({
		async start(controller) {
			const { default: sqlite3 } = await import('sqlite3');
			db = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READONLY, (err) => {
				if (err) {
					console.error('Błąd otwarcia bazy dla SSE:', err);
					controller.error(err);
					return;
				}

				db.run('PRAGMA journal_mode = WAL');

				if (!lastId) {
					db.get('SELECT MAX(id) as maxId FROM window_activity', [], (err, row) => {
						if (!err && row && row.maxId) {
							lastId = row.maxId;
						}
						startPolling();
					});
				} else {
					startPolling();
				}
			});

			function startPolling() {
				timer = setInterval(() => {
					db.all(
						`SELECT id, ts_start, ts_end, duration_ms, host, app_class, window_title, workspace, monitor, category, project, entity 
						 FROM window_activity 
						 WHERE id > ? 
						 ORDER BY id ASC`,
						[lastId],
						(err, rows) => {
							if (err) return;
							if (rows && rows.length > 0) {
								rows.forEach(row => {
									const data = `data: ${JSON.stringify(row)}\n\n`;
									controller.enqueue(new TextEncoder().encode(data));
									lastId = Math.max(lastId, row.id);
								});
							}
						}
					);
				}, 250); // szybki polling co 250ms dla lokalnego dev
			}
		},
		cancel() {
			if (timer) clearInterval(timer);
			if (db) db.close();
		}
	});

	return new Response(stream, {
		headers: {
			'Content-Type': 'text/event-stream',
			'Cache-Control': 'no-cache',
			'Connection': 'keep-alive'
		}
	});
}
