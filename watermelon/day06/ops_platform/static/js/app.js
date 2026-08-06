// ============================================================
// 运维平台 - 前端应用
// ============================================================

const API_BASE = '/api';

// ============================================================
// 状态管理
// ============================================================
const state = {
    services: [],
    deployments: [],
    alerts: [],
    stats: {},
    currentPage: 'dashboard',
    charts: {}
};

// ============================================================
// Toast 通知
// ============================================================
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icons = { success: '✅', error: '❌', info: 'ℹ️' };
    toast.innerHTML = `${icons[type] || 'ℹ️'} ${message}`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('hide');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============================================================
// API 请求
// ============================================================
async function fetchAPI(path, options = {}) {
    try {
        const res = await fetch(`${API_BASE}${path}`, {
            headers: { 'Content-Type': 'application/json' },
            ...options
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        if (res.status === 204) return null;
        return await res.json();
    } catch (e) {
        showToast(`请求失败: ${e.message}`, 'error');
        throw e;
    }
}

// ============================================================
// 仪表盘
// ============================================================
async function loadDashboard() {
    try {
        const data = await fetchAPI('/dashboard/stats');
        state.stats = data;
        
        document.getElementById('totalServices').textContent = data.services.total;
        document.getElementById('runningServices').textContent = data.services.running;
        document.getElementById('errorServices').textContent = data.services.error;
        document.getElementById('deployCount').textContent = data.deployments.total;
        document.getElementById('firingAlerts').textContent = data.alerts.firing;
        
        // 加载活动列表
        await loadActivities();
        // 加载图表
        await loadCharts();
    } catch (e) {
        console.error('加载仪表盘失败:', e);
    }
}

async function loadActivities() {
    try {
        const activities = await fetchAPI('/dashboard/recent/activities?limit=5');
        const container = document.getElementById('activityList');
        if (!activities || activities.length === 0) {
            container.innerHTML = '<div class="empty-row" style="padding:20px;text-align:center;color:var(--gray-400);">暂无活动</div>';
            return;
        }
        container.innerHTML = activities.map(a => {
            const iconMap = {
                deployment: 'deploy',
                alert: a.status === 'resolved' ? 'info' : 'alert'
            };
            const statusMap = {
                deployment: a.status === 'success' ? 'success' : 'warning',
                alert: a.status === 'firing' ? 'warning' : 'resolved'
            };
            const statusText = {
                deployment: a.status === 'success' ? '成功' : a.status,
                alert: a.status === 'firing' ? '告警' : '已解决'
            };
            return `
                <div class="activity-item">
                    <div class="activity-icon ${iconMap[a.type] || 'info'}">
                        <i class="fas ${a.type === 'deployment' ? 'fa-rocket' : a.status === 'firing' ? 'fa-exclamation-circle' : 'fa-check-circle'}"></i>
                    </div>
                    <div class="activity-content">
                        <div class="activity-text">${a.service ? `<strong>${a.service}</strong>` : ''} ${a.message || a.version ? `部署版本 <span class="tag">${a.version || 'v1.0'}</span>` : ''}</div>
                        <div class="activity-time">${timeAgo(a.time)}</div>
                    </div>
                    <div class="activity-status ${statusMap[a.type] || 'info'}">${statusText[a.type] || a.status}</div>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error('加载活动失败:', e);
    }
}

function timeAgo(isoTime) {
    const diff = Math.floor((Date.now() - new Date(isoTime).getTime()) / 1000);
    if (diff < 60) return '刚刚';
    if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
    return `${Math.floor(diff / 86400)}天前`;
}

// ============================================================
// 图表
// ============================================================
async function loadCharts() {
    try {
        const [deployTrend, alertTrend] = await Promise.all([
            fetchAPI('/dashboard/deployments/trend?days=7'),
            fetchAPI('/dashboard/alerts/trend?days=7')
        ]);
        
        // 部署趋势图
        const ctx1 = document.getElementById('deployChart').getContext('2d');
        if (state.charts.deploy) state.charts.deploy.destroy();
        state.charts.deploy = new Chart(ctx1, {
            type: 'line',
            data: {
                labels: deployTrend.map(d => d.date),
                datasets: [
                    { label: '成功', data: deployTrend.map(d => d.success), borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', fill: true, tension: 0.3 },
                    { label: '失败', data: deployTrend.map(d => d.failed), borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.1)', fill: true, tension: 0.3 }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } } }
            }
        });
        
        // 告警趋势图
        const ctx2 = document.getElementById('alertChart').getContext('2d');
        if (state.charts.alert) state.charts.alert.destroy();
        state.charts.alert = new Chart(ctx2, {
            type: 'bar',
            data: {
                labels: alertTrend.map(d => d.date),
                datasets: [
                    { label: '严重', data: alertTrend.map(d => d.critical), backgroundColor: '#ef4444', borderRadius: 4 },
                    { label: '警告', data: alertTrend.map(d => d.warning), backgroundColor: '#f59e0b', borderRadius: 4 },
                    { label: '信息', data: alertTrend.map(d => d.info), backgroundColor: '#3b82f6', borderRadius: 4 }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } } },
                scales: { x: { stacked: true }, y: { stacked: true } }
            }
        });
    } catch (e) {
        console.error('加载图表失败:', e);
    }
}

// ============================================================
// 服务管理
// ============================================================
async function loadServices() {
    try {
        const services = await fetchAPI('/services');
        state.services = services;
        renderServices(services);
    } catch (e) {
        console.error('加载服务失败:', e);
        document.getElementById('serviceGrid').innerHTML = `<div class="empty-row" style="padding:40px;text-align:center;color:var(--gray-400);">❌ 加载失败: ${e.message}</div>`;
    }
}

function renderServices(services) {
    const container = document.getElementById('serviceGrid');
    if (!services || services.length === 0) {
        container.innerHTML = `
            <div style="grid-column:1/-1;text-align:center;padding:60px 20px;color:var(--gray-400);">
                <div style="font-size:48px;margin-bottom:12px;">📭</div>
                <div style="font-size:16px;">暂无服务</div>
                <div style="font-size:13px;margin-top:4px;">点击「创建服务」开始添加</div>
            </div>
        `;
        return;
    }
    container.innerHTML = services.map(s => `
        <div class="service-card">
            <div class="service-card-header">
                <span class="service-name"><i class="fas fa-server"></i> ${s.name}</span>
                <span class="status-badge ${s.status || 'unknown'}">${s.status || '未知'}</span>
            </div>
            <div class="service-desc">${s.description || '暂无描述'}</div>
            <div class="service-meta">
                <span><i class="fas fa-tag"></i> ${s.env || 'prod'}</span>
                <span><i class="fas fa-code-branch"></i> ${s.version || 'latest'}</span>
                <span><i class="fas fa-copy"></i> ${s.replicas || 1} 副本</span>
                <span><i class="fas fa-clock"></i> ${new Date(s.created_at).toLocaleDateString()}</span>
            </div>
            <div class="service-actions">
                <button class="btn btn-primary btn-sm" onclick="viewService(${s.id})"><i class="fas fa-eye"></i> 详情</button>
                <button class="btn btn-outline btn-sm" onclick="editService(${s.id})"><i class="fas fa-edit"></i> 编辑</button>
                <button class="btn btn-danger btn-sm" onclick="deleteService(${s.id})"><i class="fas fa-trash"></i> 删除</button>
            </div>
        </div>
    `).join('');
}

// ============================================================
// 创建服务
// ============================================================
document.getElementById('createServiceBtn')?.addEventListener('click', () => {
    document.getElementById('createModal').classList.add('active');
});

document.getElementById('closeModal')?.addEventListener('click', () => {
    document.getElementById('createModal').classList.remove('active');
});

document.getElementById('cancelModal')?.addEventListener('click', () => {
    document.getElementById('createModal').classList.remove('active');
});

document.getElementById('createModal')?.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
        document.getElementById('createModal').classList.remove('active');
    }
});

document.getElementById('submitService')?.addEventListener('click', async () => {
    const name = document.getElementById('formName').value.trim();
    const description = document.getElementById('formDesc').value.trim();
    const env = document.getElementById('formEnv').value;
    const replicas = parseInt(document.getElementById('formReplicas').value) || 1;
    const endpoint = document.getElementById('formEndpoint').value.trim();
    
    if (!name) {
        showToast('请输入服务名称', 'error');
        return;
    }
    
    try {
        await fetchAPI('/services', {
            method: 'POST',
            body: JSON.stringify({ name, description, env, replicas, endpoint })
        });
        showToast(`✅ 服务 "${name}" 创建成功`, 'success');
        document.getElementById('createModal').classList.remove('active');
        document.getElementById('createServiceForm').reset();
        loadServices();
        loadDashboard();
    } catch (e) {
        showToast(`创建失败: ${e.message}`, 'error');
    }
});

// ============================================================
// 服务操作
// ============================================================
async function viewService(id) {
    showToast(`查看服务 ID: ${id}`, 'info');
}

async function editService(id) {
    showToast(`编辑服务 ID: ${id}`, 'info');
}

async function deleteService(id) {
    if (!confirm('确定要删除这个服务吗？')) return;
    try {
        await fetchAPI(`/services/${id}`, { method: 'DELETE' });
        showToast('✅ 服务已删除', 'success');
        loadServices();
        loadDashboard();
    } catch (e) {
        showToast(`删除失败: ${e.message}`, 'error');
    }
}

// ============================================================
// 导航切换
// ============================================================
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', function(e) {
        e.preventDefault();
        const page = this.dataset.page;
        if (!page) return;
        
        // 更新导航状态
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        this.classList.add('active');
        
        // 切换页面
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.getElementById(`page-${page}`)?.classList.add('active');
        
        // 更新标题
        const titles = {
            dashboard: '仪表盘',
            services: '服务管理',
            deployments: '部署管理',
            alerts: '告警管理',
            settings: '系统设置'
        };
        document.getElementById('pageTitle').textContent = titles[page] || page;
        
        // 加载对应数据
        state.currentPage = page;
        if (page === 'dashboard') loadDashboard();
        else if (page === 'services') loadServices();
        else if (page === 'deployments') loadDeployments();
        else if (page === 'alerts') loadAlerts();
    });
});

// ============================================================
// 部署管理
// ============================================================
async function loadDeployments() {
    try {
        const deployments = await fetchAPI('/deployments');
        const tbody = document.getElementById('deploymentTableBody');
        if (!deployments || deployments.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="empty-row">暂无部署记录</td></tr>';
            return;
        }
        tbody.innerHTML = deployments.map(d => `
            <tr>
                <td>#${d.id}</td>
                <td><strong>${d.service?.name || '未知'}</strong></td>
                <td><span class="tag">${d.version}</span></td>
                <td><span class="status-badge ${d.status === 'success' ? 'running' : d.status === 'failed' ? 'error' : 'unknown'}">${d.status}</span></td>
                <td>${d.deployed_by || 'system'}</td>
                <td>${d.duration ? d.duration.toFixed(1) + 's' : '-'}</td>
                <td>${new Date(d.created_at).toLocaleString()}</td>
                <td>
                    <button class="btn btn-outline btn-sm"><i class="fas fa-eye"></i></button>
                    ${d.status === 'success' ? `<button class="btn btn-primary btn-sm"><i class="fas fa-undo"></i></button>` : ''}
                </td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('加载部署失败:', e);
    }
}

// ============================================================
// 告警管理
// ============================================================
async function loadAlerts() {
    try {
        const alerts = await fetchAPI('/alerts');
        const container = document.getElementById('alertList');
        const summary = document.getElementById('alertSummary');
        
        const firing = alerts.filter(a => a.status === 'firing');
        summary.innerHTML = `共 <strong>${alerts.length}</strong> 条告警，<strong class="text-danger">${firing.length}</strong> 条未解决`;
        
        if (!alerts || alerts.length === 0) {
            container.innerHTML = '<div class="empty-row" style="padding:40px;text-align:center;color:var(--gray-400);">🎉 暂无告警，一切正常</div>';
            return;
        }
        
        container.innerHTML = alerts.map(a => `
            <div class="alert-item ${a.severity || 'info'} ${a.status === 'resolved' ? 'resolved' : ''}">
                <div class="alert-icon ${a.severity === 'critical' ? 'text-danger' : a.severity === 'warning' ? 'text-warning' : 'text-info'}">
                    <i class="fas ${a.severity === 'critical' ? 'fa-exclamation-triangle' : a.severity === 'warning' ? 'fa-exclamation-circle' : 'fa-info-circle'}"></i>
                </div>
                <div class="alert-content">
                    <div class="alert-title">${a.name}</div>
                    <div class="alert-message">${a.message || '无详细信息'}</div>
                    <div class="alert-time">${new Date(a.started_at).toLocaleString()} ${a.status === 'resolved' ? `· 已解决 ${new Date(a.resolved_at).toLocaleString()}` : ''}</div>
                </div>
                <span class="alert-severity ${a.severity || 'info'}">${a.severity || 'info'}</span>
                ${a.status === 'firing' ? `<button class="btn btn-outline btn-sm" onclick="resolveAlert(${a.id})">解决</button>` : ''}
            </div>
        `).join('');
    } catch (e) {
        console.error('加载告警失败:', e);
    }
}

async function resolveAlert(id) {
    try {
        await fetchAPI(`/alerts/${id}/resolve`, { method: 'PATCH' });
        showToast('✅ 告警已解决', 'success');
        loadAlerts();
        loadDashboard();
    } catch (e) {
        showToast(`操作失败: ${e.message}`, 'error');
    }
}

// ============================================================
// 搜索和过滤
// ============================================================
document.getElementById('serviceSearch')?.addEventListener('input', (e) => {
    const keyword = e.target.value.toLowerCase();
    const filtered = state.services.filter(s => 
        s.name.toLowerCase().includes(keyword) ||
        (s.description && s.description.toLowerCase().includes(keyword))
    );
    renderServices(filtered);
});

document.getElementById('filterEnv')?.addEventListener('change', applyFilters);
document.getElementById('filterStatus')?.addEventListener('change', applyFilters);

function applyFilters() {
    const env = document.getElementById('filterEnv').value;
    const status = document.getElementById('filterStatus').value;
    let filtered = [...state.services];
    if (env) filtered = filtered.filter(s => s.env === env);
    if (status) filtered = filtered.filter(s => s.status === status);
    renderServices(filtered);
}

// ============================================================
// 侧边栏切换（移动端）
// ============================================================
document.getElementById('menuToggle')?.addEventListener('click', () => {
    document.getElementById('sidebar').classList.toggle('open');
});

// 点击外部关闭侧边栏
document.addEventListener('click', (e) => {
    const sidebar = document.getElementById('sidebar');
    const toggle = document.getElementById('menuToggle');
    if (window.innerWidth <= 768 && 
        sidebar.classList.contains('open') &&
        !sidebar.contains(e.target) &&
        !toggle.contains(e.target)) {
        sidebar.classList.remove('open');
    }
});

// ============================================================
// 初始化
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    loadServices();
    // 预加载其他页面数据
    setTimeout(() => { loadDeployments(); }, 1000);
    setTimeout(() => { loadAlerts(); }, 1500);
});

// ============================================================
// 全屏
// ============================================================
document.querySelector('[title="全屏"]')?.addEventListener('click', () => {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen();
    } else {
        document.exitFullscreen();
    }
});

console.log('🚀 运维平台 v1.0.0 已加载');