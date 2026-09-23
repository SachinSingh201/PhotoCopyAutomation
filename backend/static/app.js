// PrintShop Admin Dashboard — Complete Application Logic

const API_BASE = '/api/v1/admin';
let currentTab = 'dashboard';
let pollInterval = null;
let searchDebounceTimer = null;
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

// Auth & API Request Helper
function getAdminToken() {
    const input = document.getElementById('admin-token-input');
    return input ? input.value.trim() : 'admin_secret_token_change_me';
}

async function apiRequest(endpoint, options = {}) {
    const token = getAdminToken();
    const headers = {
        'Content-Type': 'application/json',
        'X-Admin-Secret': token,
        ...(options.headers || {})
    };

    try {
        const res = await fetch(`${API_BASE}${endpoint}`, { ...options, headers });
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || errData.message || `HTTP ${res.status}`);
        }
        return await res.json();
    } catch (err) {
        showToast(err.message, 'error');
        throw err;
    }
}

// Toast Notifications
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/[&<>'"]/g, 
        tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
}

// Tab Switching
function initTabs() {
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            switchTab(tab);
        });
    });
}

function switchTab(tabId) {
    currentTab = tabId;
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tabId);
    });
    document.querySelectorAll('.tab-pane').forEach(p => {
        p.classList.toggle('active', p.id === `pane-${tabId}`);
    });

    refreshActiveTabData();
}

function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.add('active');
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.remove('active');
}

// 1. System Status & Top Navigation
async function fetchSystemStatus() {
    try {
        const status = await apiRequest('/system/status');
        
        // Update sidebar strip
        const dotAgent = document.getElementById('strip-dot-agent');
        const valAgent = document.getElementById('strip-val-agent');
        if (dotAgent && valAgent) {
            valAgent.textContent = status.agent_status;
            dotAgent.className = `status-dot ${status.agent_status === 'ONLINE' ? 'online' : 'paused'}`;
        }

        const dotPrinters = document.getElementById('strip-dot-printers');
        const valPrinters = document.getElementById('strip-val-printers');
        const badgePrinters = document.getElementById('sidebar-printers-badge');
        if (dotPrinters && valPrinters) {
            valPrinters.textContent = `${status.printers_connected} Connected`;
            dotPrinters.className = `status-dot ${status.printers_connected > 0 ? 'online' : 'closed'}`;
            if (badgePrinters) badgePrinters.textContent = status.printers_connected;
        }

        const dotReviews = document.getElementById('strip-dot-reviews');
        const valReviews = document.getElementById('strip-val-reviews');
        if (dotReviews && valReviews) {
            valReviews.textContent = `${status.jobs_needing_review} Jobs`;
            dotReviews.className = `status-dot ${status.jobs_needing_review > 0 ? 'paused' : 'online'}`;
        }

        // Update Dashboard Print Service Pill & Texts
        const servicePill = document.getElementById('dashboard-service-pill');
        const serviceText = document.getElementById('dashboard-service-text');
        const serviceDesc = document.getElementById('service-window-desc');
        const overrideVal = document.getElementById('service-override-val');
        const nextWinVal = document.getElementById('service-next-window');

        if (servicePill && serviceText) {
            serviceText.textContent = status.effective_print_status;
            servicePill.className = `status-pill ${status.effective_print_status === 'RUNNING' ? 'online' : (status.effective_print_status === 'PAUSED' ? 'paused' : 'closed')}`;
        }
        if (serviceDesc) serviceDesc.textContent = status.active_window;
        if (overrideVal) overrideVal.textContent = status.manual_override;
        if (nextWinVal) nextWinVal.textContent = status.next_window || '—';
    } catch (err) {
        console.error("System status error:", err);
    }
}

// 2. Global Search
function initGlobalSearch() {
    const searchInput = document.getElementById('global-search-input');
    const searchDropdown = document.getElementById('search-dropdown');
    if (!searchInput || !searchDropdown) return;

    searchInput.addEventListener('input', () => {
        clearTimeout(searchDebounceTimer);
        const q = searchInput.value.trim();
        if (!q) {
            searchDropdown.classList.remove('active');
            searchDropdown.innerHTML = '';
            return;
        }

        searchDebounceTimer = setTimeout(async () => {
            try {
                const results = await apiRequest(`/search?q=${encodeURIComponent(q)}`);
                if (!results || results.length === 0) {
                    searchDropdown.innerHTML = '<div class="p-3 text-muted text-center">No matching records found</div>';
                } else {
                    searchDropdown.innerHTML = results.map(r => `
                        <div class="search-result-item" onclick="handleSearchResultClick('${r.type}', '${r.id}')">
                            <div>
                                <div style="font-weight:700; font-size:0.88rem;">${escapeHtml(r.title)}</div>
                                <div style="font-size:0.78rem; color:var(--text-muted);">${escapeHtml(r.subtitle)}</div>
                            </div>
                            <span class="status-tag ${getStatusClass(r.status || '')}">${escapeHtml(r.type.toUpperCase())}</span>
                        </div>
                    `).join('');
                }
                searchDropdown.classList.add('active');
            } catch (err) {}
        }, 300);
    });

    document.addEventListener('click', (e) => {
        if (!searchDropdown.contains(e.target) && e.target !== searchInput) {
            searchDropdown.classList.remove('active');
        }
    });
}

function handleSearchResultClick(type, id) {
    document.getElementById('search-dropdown')?.classList.remove('active');
    if (type === 'order' || type === 'file') {
        viewOrderDetails(id);
    }
}

// 3. Notifications Bell & Dropdown
function initNotifications() {
    const btn = document.getElementById('btn-notifications');
    const dropdown = document.getElementById('notif-dropdown');
    if (!btn || !dropdown) return;

    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        dropdown.classList.toggle('active');
        fetchNotifications();
    });

    document.addEventListener('click', (e) => {
        if (!dropdown.contains(e.target) && e.target !== btn) {
            dropdown.classList.remove('active');
        }
    });
}

async function fetchNotifications() {
    try {
        const notifs = await apiRequest('/notifications');
        const badge = document.getElementById('notif-badge-count');
        const unreadText = document.getElementById('notif-unread-text');
        const container = document.getElementById('notif-list-container');

        const unreadCount = notifs.filter(n => !n.is_read).length;
        if (badge) {
            badge.style.display = unreadCount > 0 ? 'flex' : 'none';
            badge.textContent = unreadCount;
        }
        if (unreadText) unreadText.textContent = `${unreadCount} unread`;

        if (!container) return;
        if (!notifs || notifs.length === 0) {
            container.innerHTML = '<p class="text-muted text-center p-3">No notifications</p>';
            return;
        }

        container.innerHTML = notifs.map(n => `
            <div class="notif-item ${!n.is_read ? 'unread' : ''}" onclick="readNotification('${n.id}')">
                <div class="notif-title">${escapeHtml(n.title)}</div>
                <div class="notif-msg">${escapeHtml(n.message || n.type)}</div>
                <div style="font-size:0.72rem; color:var(--text-muted);">${new Date(n.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</div>
            </div>
        `).join('');
    } catch (err) {}
}

async function readNotification(id) {
    try {
        await apiRequest(`/notifications/${id}/read`, { method: 'PATCH' });
        fetchNotifications();
    } catch (err) {}
}

// 4. Dashboard KPIs, Currently Printing, and Needs Attention
async function fetchDashboardData() {
    try {
        // 1. KPIs
        const stats = await apiRequest('/dashboard/summary');
        document.getElementById('kpi-orders-today').textContent = stats.orders_today;
        document.getElementById('kpi-waiting').textContent = stats.waiting_count;
        document.getElementById('kpi-printing').textContent = stats.printing_count;
        document.getElementById('kpi-ready').textContent = stats.ready_count;
        document.getElementById('kpi-revenue').textContent = `₹${stats.revenue_today.toFixed(2)}`;
        document.getElementById('sidebar-queue-badge').textContent = stats.waiting_count + stats.printing_count;

        // 2. Currently Printing Live Widget
        const curJob = await apiRequest('/print-jobs/current');
        const curBody = document.getElementById('current-job-content');
        const curBadge = document.getElementById('current-job-badge');

        if (curBody && curBadge) {
            if (!curJob.job_found) {
                curBadge.textContent = 'IDLE';
                curBadge.className = 'status-tag tag-queued';
                curBody.innerHTML = `
                    <div class="empty-state-box">
                        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
                        <p>Printer is idle. Next eligible job will spool automatically.</p>
                    </div>
                `;
            } else {
                curBadge.textContent = 'PRINTING NOW';
                curBadge.className = 'status-tag tag-printing';
                const compTime = curJob.estimated_completion_at ? new Date(curJob.estimated_completion_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : 'Shortly';
                curBody.innerHTML = `
                    <div>
                        <div class="job-live-header">
                            <div>
                                <b style="font-size:1.1rem;">Token ${escapeHtml(curJob.pickup_token || '—')}</b>
                                <span class="font-mono text-muted" style="margin-left:8px;">(${escapeHtml(curJob.order_code)})</span>
                            </div>
                            <span class="status-tag tag-printing">SPOOLING</span>
                        </div>
                        <div style="font-size:0.88rem; margin-bottom:8px;">
                            File: <b>${escapeHtml(curJob.primary_filename)}</b> (${curJob.total_pages} pages, ${curJob.color_mode.toUpperCase()}, ${curJob.default_sides.toUpperCase()})
                        </div>
                        <div class="text-muted" style="font-size:0.82rem;">
                            Started: ${new Date(curJob.started_at).toLocaleTimeString()} • Estimated completion: <b>${compTime}</b>
                        </div>
                    </div>
                `;
            }
        }

        // 3. Needs Attention Panel
        const alerts = await apiRequest('/alerts?status=open');
        const attBadge = document.getElementById('attention-badge-count');
        const attContainer = document.getElementById('attention-list-container');

        if (attBadge) attBadge.textContent = `${alerts.length} Open`;
        if (attContainer) {
            if (!alerts || alerts.length === 0) {
                attContainer.innerHTML = `
                    <div class="empty-state-box">
                        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                        <p>All clear! No failed jobs or manual interventions required.</p>
                    </div>
                `;
            } else {
                attContainer.innerHTML = alerts.map(a => `
                    <div class="attention-item">
                        <div class="attention-item-info">
                            <span class="attention-title">${escapeHtml(a.title)}</span>
                            <span class="attention-desc">${escapeHtml(a.description)}</span>
                        </div>
                        ${a.job_id ? `<button class="btn btn-sm btn-primary" onclick="handleManualPrint('${a.job_id}')">Print Manually</button>` : ''}
                    </div>
                `).join('');
            }
        }

        // 4. Queue preview (top 5)
        const previewQueue = await apiRequest('/queue?limit=5');
        renderQueueTable('dashboard-queue-table', previewQueue, true);
    } catch (err) {
        console.error("Dashboard data fetch error:", err);
    }
}

// 5. Queue View
async function fetchQueueData() {
    const search = document.getElementById('queue-search-input')?.value || '';
    let url = '/queue?';
    if (search) url += `search=${encodeURIComponent(search)}`;

    try {
        const queue = await apiRequest(url);
        renderQueueTable('full-queue-table', queue, false);
    } catch (err) {
        console.error("Queue fetch error:", err);
    }
}

function renderQueueTable(tableId, items, isCompact) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!tbody) return;

    if (!items || items.length === 0) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="${isCompact ? 6 : 10}">Queue is currently empty</td></tr>`;
        return;
    }

    tbody.innerHTML = items.map(job => {
        const statusClass = getStatusClass(job.status);
        const etaMins = Math.ceil(job.eta_seconds / 60);

        let actions = `
            <div class="btn-action-group">
                ${job.is_held ? 
                    `<button class="btn btn-sm btn-secondary" onclick="handleResumeJob('${job.job_id}')">Resume</button>` : 
                    `<button class="btn btn-sm btn-ghost" onclick="handleHoldJob('${job.job_id}')">Hold</button>`
                }
                <button class="btn btn-sm btn-primary" onclick="handleManualPrint('${job.job_id}')">Manual Print</button>
                ${job.status === 'FAILED' ? `<button class="btn btn-sm btn-secondary" onclick="handleRetryJob('${job.job_id}')">Retry</button>` : ''}
                <button class="btn btn-sm btn-ghost text-coral" onclick="handleCancelJob('${job.job_id}')">Cancel</button>
            </div>
        `;

        if (isCompact) {
            return `
                <tr>
                    <td><span class="font-mono font-bold">${escapeHtml(job.pickup_token || '—')}</span></td>
                    <td><a href="javascript:void(0)" onclick="viewOrderDetails('${job.order_id}')" class="font-mono">${escapeHtml(job.order_code)}</a></td>
                    <td>${job.total_pages}</td>
                    <td><span class="status-tag ${statusClass}">${job.status}</span></td>
                    <td>~${etaMins}m</td>
                    <td><button class="btn btn-sm btn-primary" onclick="handleManualPrint('${job.job_id}')">Print</button></td>
                </tr>
            `;
        } else {
            return `
                <tr>
                    <td>#${job.queue_position}</td>
                    <td><span class="font-mono font-bold" style="color:var(--accent-emerald)">${escapeHtml(job.pickup_token || '—')}</span></td>
                    <td><a href="javascript:void(0)" onclick="viewOrderDetails('${job.order_id}')" class="font-mono" style="color:var(--accent-blue)">${escapeHtml(job.order_code)}</a></td>
                    <td>${escapeHtml(job.customer_name || job.customer_phone || 'Customer')}</td>
                    <td>${job.file_count}</td>
                    <td>${job.total_pages}</td>
                    <td><span class="status-tag tag-${job.payment_status.toLowerCase()}">${job.payment_status}</span></td>
                    <td><span class="status-tag ${statusClass}">${job.status}</span></td>
                    <td>~${etaMins} mins</td>
                    <td>${actions}</td>
                </tr>
            `;
        }
    }).join('');
}

function getStatusClass(status) {
    switch (status) {
        case 'QUEUED': return 'tag-queued';
        case 'PRINTING': return 'tag-printing';
        case 'PRINTED': case 'READY_FOR_PICKUP': case 'COLLECTED': return 'tag-ready';
        case 'FAILED': case 'PRINT_FAILED': return 'tag-failed';
        case 'HELD': return 'tag-held';
        case 'WAITING_FOR_PRINT_WINDOW': return 'tag-waiting';
        default: return 'tag-queued';
    }
}

// Queue Actions
async function handleHoldJob(jobId) {
    const reason = prompt("Enter hold reason (optional):", "Shop owner hold");
    if (reason === null) return;
    try {
        await apiRequest(`/queue/${jobId}/hold`, {
            method: 'POST',
            body: JSON.stringify({ reason: reason || 'Admin hold' })
        });
        showToast("Job placed on hold", "warning");
        fetchQueueData();
        fetchDashboardData();
    } catch (err) {}
}

async function handleResumeJob(jobId) {
    try {
        await apiRequest(`/queue/${jobId}/resume`, { method: 'POST' });
        showToast("Job resumed in queue", "success");
        fetchQueueData();
        fetchDashboardData();
    } catch (err) {}
}

async function handleRetryJob(jobId) {
    try {
        await apiRequest(`/queue/${jobId}/retry`, { method: 'POST' });
        showToast("Job re-queued for retry", "success");
        fetchQueueData();
        fetchDashboardData();
    } catch (err) {}
}

async function handleManualPrint(jobId) {
    if (!confirm("Are you sure you want to mark this job as MANUALLY printed and ready for pickup?")) return;
    try {
        await apiRequest(`/queue/${jobId}/manual-print`, { method: 'POST' });
        showToast("Manual print recorded! Customer notified.", "success");
        fetchQueueData();
        fetchDashboardData();
    } catch (err) {}
}

async function handleCancelJob(jobId) {
    if (!confirm("Are you sure you want to cancel this print job?")) return;
    try {
        await apiRequest(`/queue/${jobId}/cancel`, { method: 'POST' });
        showToast("Job cancelled", "warning");
        fetchQueueData();
        fetchDashboardData();
    } catch (err) {}
}

// 6. Orders & History Views
async function fetchOrdersData() {
    const statusFilter = document.getElementById('order-status-filter')?.value || '';
    const search = document.getElementById('order-search-input')?.value || '';

    let url = '/orders?';
    if (statusFilter) url += `status=${encodeURIComponent(statusFilter)}&`;
    if (search) url += `search=${encodeURIComponent(search)}`;

    try {
        const orders = await apiRequest(url);
        const tbody = document.querySelector('#orders-table tbody');
        if (!tbody) return;

        if (!orders || orders.length === 0) {
            tbody.innerHTML = `<tr class="empty-row"><td colspan="9">No active orders matching criteria</td></tr>`;
            return;
        }

        tbody.innerHTML = orders.map(ord => {
            const dateStr = new Date(ord.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            return `
                <tr>
                    <td><span class="font-mono font-bold">${escapeHtml(ord.order_code)}</span></td>
                    <td><span class="font-mono font-bold" style="color:var(--accent-emerald)">${escapeHtml(ord.pickup_token || '—')}</span></td>
                    <td>${escapeHtml(ord.user ? (ord.user.display_name || ord.user.phone_number) : 'Customer')}</td>
                    <td>${dateStr}</td>
                    <td>${ord.files ? ord.files.length : 0}</td>
                    <td>${ord.total_pages}</td>
                    <td>₹${ord.total_amount.toFixed(2)}</td>
                    <td><span class="status-tag ${getStatusClass(ord.status)}">${ord.status}</span></td>
                    <td>
                        <div class="btn-action-group">
                            <button class="btn btn-sm btn-ghost" onclick="viewOrderDetails('${ord.id}')">Inspect</button>
                            ${ord.status === 'READY_FOR_PICKUP' ? 
                                `<button class="btn btn-sm btn-primary" onclick="markOrderCollected('${ord.id}')">Collected</button>` : ''}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        console.error("Orders fetch error:", err);
    }
}

async function fetchHistoryData() {
    try {
        const orders = await apiRequest('/orders?status=COLLECTED,CANCELLED,EXPIRED');
        const tbody = document.querySelector('#history-table tbody');
        if (!tbody) return;

        if (!orders || orders.length === 0) {
            tbody.innerHTML = `<tr class="empty-row"><td colspan="8">No history records found</td></tr>`;
            return;
        }

        tbody.innerHTML = orders.map(ord => {
            const dateStr = new Date(ord.created_at).toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
            return `
                <tr>
                    <td><span class="font-mono font-bold">${escapeHtml(ord.order_code)}</span></td>
                    <td><span class="font-mono">${escapeHtml(ord.pickup_token || '—')}</span></td>
                    <td>${escapeHtml(ord.user ? (ord.user.display_name || ord.user.phone_number) : 'Customer')}</td>
                    <td>${dateStr}</td>
                    <td>${ord.total_pages}</td>
                    <td>₹${ord.total_amount.toFixed(2)}</td>
                    <td><span class="status-tag ${getStatusClass(ord.status)}">${ord.status}</span></td>
                    <td><button class="btn btn-sm btn-ghost" onclick="viewOrderDetails('${ord.id}')">View</button></td>
                </tr>
            `;
        }).join('');
    } catch (err) {}
}

async function markOrderCollected(orderId) {
    try {
        await apiRequest(`/orders/${orderId}/collected`, { method: 'POST' });
        showToast("Order marked as collected!", "success");
        fetchOrdersData();
        fetchDashboardData();
    } catch (err) {}
}

async function viewOrderDetails(orderId) {
    try {
        const order = await apiRequest(`/orders/${orderId}`);
        const modalBody = document.getElementById('modal-order-body');
        const modalTitle = document.getElementById('modal-order-title');
        if (!modalBody) return;

        modalTitle.textContent = `Order: ${order.order_code} (Token: ${order.pickup_token || 'N/A'})`;

        const filesHtml = (order.files || []).map(f => {
            const cfg = f.configuration || {};
            return `
                <div class="glass-card mt-2 p-3">
                    <div style="display:flex; justify-content:space-between; font-weight:700;">
                        <span>${f.upload_sequence}. ${escapeHtml(f.original_filename)}</span>
                        <span>${f.page_count} pages</span>
                    </div>
                    <div style="font-size:0.8rem; color:var(--text-muted); margin-top:4px;">
                        Color: <b>${cfg.color_mode || 'bw'}</b> | Sides: <b>${cfg.default_sides || 'single'}</b> | Rules: ${cfg.rules_json || '[]'}
                    </div>
                </div>
            `;
        }).join('');

        modalBody.innerHTML = `
            <div class="form-grid">
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                    <div><span class="text-muted">Status:</span> <span class="status-tag ${getStatusClass(order.status)}">${order.status}</span></div>
                    <div><span class="text-muted">Customer:</span> <b>${escapeHtml(order.user?.display_name || order.user?.phone_number || 'N/A')}</b></div>
                    <div><span class="text-muted">Total Pages:</span> <b>${order.total_pages}</b></div>
                    <div><span class="text-muted">Amount:</span> <b>₹${order.total_amount.toFixed(2)}</b></div>
                    <div><span class="text-muted">Created:</span> ${new Date(order.created_at).toLocaleString()}</div>
                </div>

                <h4 class="mt-4">Attached Documents (${order.files ? order.files.length : 0})</h4>
                ${filesHtml || '<p class="text-muted">No files attached</p>'}

                <h4 class="mt-4">Deterministic Pricing Snapshot</h4>
                <pre class="timeline-meta" style="white-space:pre-wrap;">${escapeHtml(order.price_snapshot || 'No price snapshot')}</pre>
            </div>
        `;

        openModal('order-detail-modal');
    } catch (err) {}
}

// 7. Printers View
async function fetchPrintersData() {
    try {
        const printers = await apiRequest('/printers');
        const connectedGrid = document.getElementById('connected-printers-grid');
        const availableGrid = document.getElementById('available-printers-grid');

        const connected = printers.filter(p => p.status === 'connected');
        const available = printers.filter(p => p.status !== 'connected');

        if (connectedGrid) {
            if (connected.length === 0) {
                connectedGrid.innerHTML = '<div class="glass-card text-muted text-center p-3">No printers currently connected</div>';
            } else {
                connectedGrid.innerHTML = connected.map(p => `
                    <div class="printer-card ${p.is_default ? 'default-printer' : ''}">
                        <div class="printer-header">
                            <div>
                                <div class="printer-name">${escapeHtml(p.name)}</div>
                                <div class="printer-model">${escapeHtml(p.model)} • ${p.connection_type}</div>
                            </div>
                            <span class="status-pill online"><span class="status-dot"></span> CONNECTED</span>
                        </div>
                        <div class="printer-meter-row">
                            <span>Paper Tray: <b>${escapeHtml(p.paper_tray_status || 'OK')}</b></span>
                            <span>Toner: <b>${p.toner_level !== null ? p.toner_level + '%' : 'N/A'}</b></span>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:6px;">
                            <label style="display:flex; align-items:center; gap:8px; font-size:0.82rem; cursor:pointer;">
                                <input type="checkbox" onchange="toggleDefaultPrinter('${p.id}', this.checked)" ${p.is_default ? 'checked' : ''}>
                                Default for auto-print
                            </label>
                            <div class="btn-action-group">
                                <button class="btn btn-sm btn-secondary" onclick="testPrint('${p.id}')">Test Page</button>
                                <button class="btn btn-sm btn-ghost text-coral" onclick="disconnectPrinter('${p.id}')">Disconnect</button>
                            </div>
                        </div>
                    </div>
                `).join('');
            }
        }

        if (availableGrid) {
            if (available.length === 0) {
                availableGrid.innerHTML = '<div class="glass-card text-muted text-center p-3">No other printers discovered</div>';
            } else {
                availableGrid.innerHTML = available.map(p => `
                    <div class="printer-card">
                        <div class="printer-header">
                            <div>
                                <div class="printer-name">${escapeHtml(p.name)}</div>
                                <div class="printer-model">${escapeHtml(p.model)} • ${p.connection_type} ${p.ip_address ? `(${p.ip_address})` : ''}</div>
                            </div>
                            <span class="status-tag ${p.status === 'driver_missing' ? 'tag-failed' : 'tag-queued'}">${p.status.toUpperCase()}</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
                            ${p.status === 'driver_missing' ? 
                                `<span class="text-muted" style="font-size:0.78rem;">Driver missing. Install via Print Agent setup.</span>` :
                                `<span class="text-muted" style="font-size:0.78rem;">Ready to connect</span>`
                            }
                            <button class="btn btn-sm btn-primary" ${p.status === 'driver_missing' ? 'disabled' : ''} onclick="connectPrinter('${p.id}')">Connect</button>
                        </div>
                    </div>
                `).join('');
            }
        }
    } catch (err) {
        console.error("Printers fetch error:", err);
    }
}

async function scanPrintersTrigger() {
    const btnText = document.getElementById('scan-btn-text');
    if (btnText) btnText.textContent = 'Scanning...';
    try {
        await apiRequest('/printers/scan', { method: 'POST' });
        showToast("Printer discovery scan completed!", "success");
        await fetchPrintersData();
        await fetchSystemStatus();
    } finally {
        if (btnText) btnText.textContent = 'Scan for Printers';
    }
}

async function toggleDefaultPrinter(id, isDefault) {
    try {
        await apiRequest(`/printers/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_default: isDefault })
        });
        showToast("Default printer updated", "success");
        fetchPrintersData();
    } catch (err) {}
}

async function connectPrinter(id) {
    try {
        await apiRequest(`/printers/${id}/connect`, { method: 'POST' });
        showToast("Printer connected successfully", "success");
        fetchPrintersData();
        fetchSystemStatus();
    } catch (err) {}
}

async function disconnectPrinter(id) {
    try {
        await apiRequest(`/printers/${id}/disconnect`, { method: 'POST' });
        showToast("Printer disconnected", "warning");
        fetchPrintersData();
        fetchSystemStatus();
    } catch (err) {}
}

async function testPrint(id) {
    try {
        await apiRequest(`/printers/${id}/test-page`, { method: 'POST' });
        showToast("Test page sent to printer spooler!", "success");
    } catch (err) {}
}

// 8. Schedule View
async function fetchScheduleData() {
    try {
        const schedules = await apiRequest('/schedule');
        const container = document.getElementById('weekly-schedule-container');
        if (container) {
            container.innerHTML = schedules.map(s => `
                <div class="schedule-row" data-day="${s.day_of_week}">
                    <span class="schedule-day">${s.day_name || DAY_NAMES[s.day_of_week]}</span>
                    <div class="schedule-inputs">
                        <input type="time" class="form-input sched-open" value="${s.open_time}">
                        <span>to</span>
                        <input type="time" class="form-input sched-close" value="${s.close_time}">
                        <label style="display:flex; align-items:center; gap:6px; margin-left:12px; font-size:0.85rem;">
                            <input type="checkbox" class="sched-enabled" ${s.enabled ? 'checked' : ''}> Open
                        </label>
                    </div>
                </div>
            `).join('');
        }

        const exceptions = await apiRequest('/schedule/exceptions');
        const excTbody = document.querySelector('#exceptions-table tbody');
        if (excTbody) {
            if (!exceptions || exceptions.length === 0) {
                excTbody.innerHTML = `<tr class="empty-row"><td colspan="5">No schedule exceptions configured</td></tr>`;
            } else {
                excTbody.innerHTML = exceptions.map(e => `
                    <tr>
                        <td class="font-mono">${e.exception_date}</td>
                        <td><span class="status-tag ${e.exception_type === 'CLOSED' ? 'tag-held' : 'tag-queued'}">${e.exception_type}</span></td>
                        <td>${e.open_time && e.close_time ? `${e.open_time} - ${e.close_time}` : 'All Day'}</td>
                        <td>${escapeHtml(e.reason || '—')}</td>
                        <td><button class="btn btn-sm btn-ghost text-coral" onclick="deleteException('${e.id}')">Delete</button></td>
                    </tr>
                `).join('');
            }
        }
    } catch (err) {}
}

async function saveWeeklySchedule() {
    const rows = document.querySelectorAll('.schedule-row');
    const schedules = Array.from(rows).map(r => ({
        day_of_week: parseInt(r.dataset.day),
        open_time: r.querySelector('.sched-open').value,
        close_time: r.querySelector('.sched-close').value,
        enabled: r.querySelector('.sched-enabled').checked
    }));

    try {
        await apiRequest('/schedule', {
            method: 'PUT',
            body: JSON.stringify({ schedules })
        });
        showToast("Weekly operating hours saved!", "success");
        fetchSystemStatus();
    } catch (err) {}
}

async function deleteException(id) {
    if (!confirm("Are you sure you want to remove this exception?")) return;
    try {
        await apiRequest(`/schedule/exceptions/${id}`, { method: 'DELETE' });
        showToast("Exception deleted", "success");
        fetchScheduleData();
        fetchSystemStatus();
    } catch (err) {}
}

// 9. Reports View
async function fetchReportsData() {
    try {
        const rep = await apiRequest('/reports/summary');
        document.getElementById('rep-total-rev').textContent = `₹${rep.total_revenue_7d.toFixed(2)}`;
        document.getElementById('rep-total-orders').textContent = rep.total_orders_7d;
        document.getElementById('rep-total-pages').textContent = rep.total_pages_7d;
        document.getElementById('rep-avg-val').textContent = `₹${rep.avg_order_value.toFixed(2)}`;

        const revTbody = document.querySelector('#rep-revenue-table tbody');
        if (revTbody) {
            revTbody.innerHTML = rep.daily_revenue.map(d => `
                <tr>
                    <td><b>${d.date}</b></td>
                    <td>₹${d.amount.toFixed(2)}</td>
                </tr>
            `).join('');
        }

        const volTbody = document.querySelector('#rep-volume-table tbody');
        if (volTbody) {
            volTbody.innerHTML = rep.daily_volume.map(d => `
                <tr>
                    <td><b>${d.date}</b></td>
                    <td>${d.orders} orders</td>
                </tr>
            `).join('');
        }
    } catch (err) {}
}

// 10. Settings View
async function fetchSettingsData() {
    try {
        const shop = await apiRequest('/settings/shop');
        document.getElementById('setting-shop-name').value = shop.shop_name;
        document.getElementById('setting-timezone').value = shop.timezone;
        document.getElementById('setting-currency').value = shop.currency;
        document.getElementById('setting-accept-orders').checked = shop.order_acceptance_enabled;

        const pricing = await apiRequest('/pricing');
        document.getElementById('price-base-charge').value = pricing.base_charge;
        document.getElementById('price-bw-single').value = pricing.bw_single;
        document.getElementById('price-bw-double').value = pricing.bw_double;
        document.getElementById('price-color-single').value = pricing.color_single;
        document.getElementById('price-color-double').value = pricing.color_double;
    } catch (err) {}
}

async function saveShopSettings() {
    const payload = {
        shop_name: document.getElementById('setting-shop-name').value,
        timezone: document.getElementById('setting-timezone').value,
        currency: document.getElementById('setting-currency').value,
        order_acceptance_enabled: document.getElementById('setting-accept-orders').checked
    };
    try {
        await apiRequest('/settings/shop', {
            method: 'PUT',
            body: JSON.stringify(payload)
        });
        showToast("Shop profile updated!", "success");
    } catch (err) {}
}

async function savePricingRates() {
    const payload = {
        base_charge: parseFloat(document.getElementById('price-base-charge').value),
        bw_single: parseFloat(document.getElementById('price-bw-single').value),
        bw_double: parseFloat(document.getElementById('price-bw-double').value),
        color_single: parseFloat(document.getElementById('price-color-single').value),
        color_double: parseFloat(document.getElementById('price-color-double').value),
        currency: 'INR'
    };
    try {
        await apiRequest('/pricing', {
            method: 'PUT',
            body: JSON.stringify(payload)
        });
        showToast("Pricing parameters updated!", "success");
    } catch (err) {}
}

// 11. Service Controls
async function triggerEmergencyStop() {
    const confirmText = prompt("EMERGENCY STOP will halt all print job dispatches.\nType 'EMERGENCY' to confirm:");
    if (confirmText !== 'EMERGENCY') return;

    try {
        await apiRequest('/printing/emergency-stop', {
            method: 'POST',
            body: JSON.stringify({ reason: 'Emergency Stop Triggered by Admin' })
        });
        showToast("EMERGENCY STOP ACTIVATED", "error");
        fetchSystemStatus();
        fetchDashboardData();
    } catch (err) {}
}

async function triggerStartNow() {
    try {
        await apiRequest('/printing/start', { method: 'POST', body: JSON.stringify({ reason: 'Admin forced open' }) });
        showToast("Print service forced OPEN", "success");
        fetchSystemStatus();
        fetchDashboardData();
    } catch (err) {}
}

async function triggerPauseService() {
    try {
        await apiRequest('/printing/stop', { method: 'POST', body: JSON.stringify({ reason: 'Admin paused printing' }) });
        showToast("Print service PAUSED", "warning");
        fetchSystemStatus();
        fetchDashboardData();
    } catch (err) {}
}

async function triggerResumeSchedule() {
    try {
        await apiRequest('/printing/resume-schedule', { method: 'POST' });
        showToast("Returned to automatic schedule", "success");
        fetchSystemStatus();
        fetchDashboardData();
    } catch (err) {}
}

// Refresh Active Tab
function refreshActiveTabData() {
    fetchSystemStatus();
    if (currentTab === 'dashboard') fetchDashboardData();
    if (currentTab === 'queue') fetchQueueData();
    if (currentTab === 'orders') fetchOrdersData();
    if (currentTab === 'history') fetchHistoryData();
    if (currentTab === 'printers') fetchPrintersData();
    if (currentTab === 'schedule') fetchScheduleData();
    if (currentTab === 'reports') fetchReportsData();
    if (currentTab === 'settings') fetchSettingsData();
}

// Initialization on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initGlobalSearch();
    initNotifications();

    // Top Controls
    document.getElementById('btn-emergency-stop')?.addEventListener('click', triggerEmergencyStop);
    document.getElementById('btn-service-start')?.addEventListener('click', triggerStartNow);
    document.getElementById('btn-service-pause')?.addEventListener('click', triggerPauseService);
    document.getElementById('btn-service-resume')?.addEventListener('click', triggerResumeSchedule);

    // Filters
    document.getElementById('order-status-filter')?.addEventListener('change', fetchOrdersData);
    document.getElementById('order-search-input')?.addEventListener('input', fetchOrdersData);
    document.getElementById('queue-search-input')?.addEventListener('input', fetchQueueData);

    // Printers
    document.getElementById('btn-scan-printers')?.addEventListener('click', scanPrintersTrigger);

    // Schedule & Settings saves
    document.getElementById('btn-save-schedule')?.addEventListener('click', saveWeeklySchedule);
    document.getElementById('btn-save-shop-settings')?.addEventListener('click', saveShopSettings);
    document.getElementById('btn-save-pricing')?.addEventListener('click', savePricingRates);

    // Add Exception modal
    document.getElementById('btn-open-add-exception')?.addEventListener('click', () => openModal('add-exception-modal'));
    document.getElementById('exc-type')?.addEventListener('change', (e) => {
        const hoursGroup = document.getElementById('exc-hours-group');
        if (hoursGroup) hoursGroup.style.display = e.target.value === 'SPECIAL_HOURS' ? 'block' : 'none';
    });

    document.getElementById('exception-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const dateVal = document.getElementById('exc-date').value;
        const typeVal = document.getElementById('exc-type').value;
        const reasonVal = document.getElementById('exc-reason').value;
        let openTime = null, closeTime = null;
        if (typeVal === 'SPECIAL_HOURS') {
            openTime = document.getElementById('exc-open').value;
            closeTime = document.getElementById('exc-close').value;
        }

        try {
            await apiRequest('/schedule/exceptions', {
                method: 'POST',
                body: JSON.stringify({
                    exception_date: dateVal,
                    exception_type: typeVal,
                    open_time: openTime,
                    close_time: closeTime,
                    reason: reasonVal
                })
            });
            showToast("Schedule exception created!", "success");
            closeModal('add-exception-modal');
            fetchScheduleData();
            fetchSystemStatus();
        } catch (err) {}
    });

    // Initial load
    refreshActiveTabData();

    // 5-second polling loop
    pollInterval = setInterval(refreshActiveTabData, 5000);
});
