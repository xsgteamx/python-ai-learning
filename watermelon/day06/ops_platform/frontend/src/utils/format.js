// Utility helpers migrated from the original index.html inline JS.

export function escHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[ch]))
}

// Lightweight HTML escape used in template strings (creates a text node and reads innerHTML).
export function esc(s) {
    if (!s) return ''
    const d = document.createElement('div')
    d.textContent = s
    return d.innerHTML
}

// Backend SQLite func.now() returns UTC strings without a timezone marker; append Z
// before parsing so the value is interpreted as UTC and displayed in Beijing time.
export function formatAuditTime(value) {
    if (!value) return '-'
    const text = String(value)
    const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(text)
    const date = new Date(hasTimezone ? text : text + 'Z')
    if (Number.isNaN(date.getTime())) return text
    return date.toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour12: false,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    })
}

export function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB'
}

export function joinRemotePath(dir, name) {
    if (!dir || dir === '.') return name
    return dir.replace(/\/+$/, '') + '/' + name
}

export function getRemoteDirFromPath(path) {
    if (!path) return ''
    if (path.endsWith('/')) return path
    const index = path.lastIndexOf('/')
    if (index <= 0) return ''
    return path.slice(0, index)
}
