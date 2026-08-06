import { ref, computed, reactive } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { useAuth } from './useAuth'
import { useApi } from './useApi'
import { escHtml } from '../utils/format'
import { toast } from './useToast'

// xterm.js theme shared by every SSH terminal session.
export const SSH_TERM_THEME = {
    background: '#0f172a',
    foreground: '#d4d4d4',
    cursor: '#ffffff',
    selectionBackground: '#334155',
    black: '#000000',
    red: '#ef4444',
    green: '#22c55e',
    yellow: '#f59e0b',
    blue: '#3b82f6',
    magenta: '#a855f7',
    cyan: '#06b6d4',
    white: '#e5e7eb',
    brightBlack: '#64748b',
    brightRed: '#f87171',
    brightGreen: '#4ade80',
    brightYellow: '#fbbf24',
    brightBlue: '#60a5fa',
    brightMagenta: '#c084fc',
    brightCyan: '#22d3ee',
    brightWhite: '#ffffff'
}

// Singleton state shared across components (SshModal + App float stack + views).
const sessions = reactive({})          // sessionId -> session object
const sessionOrder = ref([])           // creation order, used to render tabs/floats
const activeSessionId = ref(null)
const sshModalVisible = ref(false)
let terminalSeq = 0

function isTerminalConnected(session) {
    return session && session.socket && session.socket.readyState === WebSocket.OPEN
}

export function useTerminal() {
    const { token } = useAuth()
    const { rawFetch, API } = useApi()

    const activeSession = computed(() => (activeSessionId.value ? sessions[activeSessionId.value] : null))
    const currentSshAssetId = computed(() => activeSession.value?.assetId || null)
    // Sessions minimized into the float stack: all sessions except the active one
    // when the SSH modal is open.
    const floatSessions = computed(() => {
        if (!sshModalVisible.value) return sessionOrder.value.map(id => sessions[id])
        return sessionOrder.value
            .filter(id => id !== activeSessionId.value)
            .map(id => sessions[id])
    })

    function getActiveTerminal() {
        return activeSession.value
    }

    function appendTerminal(text, cls, sessionId) {
        const session = sessionId ? sessions[sessionId] : getActiveTerminal()
        if (!session || !session.term) return
        const colorMap = { info: '\x1b[36m', err: '\x1b[31m', cmd: '\x1b[32m' }
        const prefix = colorMap[cls] || ''
        const suffix = cls ? '\x1b[0m' : ''
        session.term.write(prefix + text + suffix)
    }

    function setSshStatus(connected, text) {
        const session = getActiveTerminal()
        if (session) session.statusText = text
        if (session) session.connected = connected
    }

    function fitTerminal() {
        const session = getActiveTerminal()
        if (!session || !session.fitAddon) return
        setTimeout(() => {
            try {
                session.fitAddon.fit()
                if (isTerminalConnected(session)) {
                    session.socket.send(`__resize__:${session.term.cols}:${session.term.rows}`)
                }
            } catch (e) { /* ignore */ }
        }, 0)
    }

    function focusTerminal() {
        const session = getActiveTerminal()
        if (session && session.term) session.term.focus()
    }

    function setActiveTerminal(sessionId) {
        const session = sessions[sessionId]
        if (!session) return
        activeSessionId.value = sessionId
        Object.values(sessions).forEach(s => {
            if (s.container) s.container.classList.toggle('active', s.id === sessionId)
        })
        setSshStatus(isTerminalConnected(session), session.statusText || (isTerminalConnected(session) ? '已连接' : '未连接'))
    }

    function createTerminalSession(assetId, hostname, terminalEl) {
        if (!terminalEl) return null
        const sessionId = `term_${Date.now()}_${++terminalSeq}`
        const container = document.createElement('div')
        container.className = 'terminal-session'
        container.dataset.sessionId = sessionId
        terminalEl.appendChild(container)

        const term = new Terminal({
            cursorBlink: true,
            convertEol: true,
            scrollback: 5000,
            fontFamily: 'Cascadia Code, Fira Code, Consolas, "Microsoft YaHei", monospace',
            fontSize: 13,
            lineHeight: 1.2,
            theme: SSH_TERM_THEME
        })
        const fitAddon = new FitAddon.FitAddon()
        term.loadAddon(fitAddon)
        term.open(container)

        const session = reactive({
            id: sessionId,
            assetId,
            hostname,
            title: `SSH终端 - ${hostname} #${terminalSeq}`,
            socket: null,
            term,
            fitAddon,
            container,
            connected: false,
            statusText: '未连接',
            inputDisposable: null,
            resizeDisposable: null
        })
        session.inputDisposable = term.onData(data => sendTerminalData(data, true, sessionId))
        session.resizeDisposable = term.onResize(size => {
            if (isTerminalConnected(session)) {
                session.socket.send(`__resize__:${size.cols}:${size.rows}`)
            }
        })
        sessions[sessionId] = session
        sessionOrder.value.push(sessionId)
        appendTerminal('已就绪，点击“连接终端”建立交互式 SSH 会话。\r\n', 'info', sessionId)
        return session
    }

    function openSshModal(assetId, hostname, terminalEl) {
        const session = createTerminalSession(assetId, hostname, terminalEl)
        if (!session) return null
        setActiveTerminal(session.id)
        sshModalVisible.value = true
        setTimeout(() => { fitTerminal(); focusTerminal() }, 50)
        return session
    }

    function minimizeSshModal() {
        sshModalVisible.value = false
    }

    function restoreSshModal(sessionId) {
        const targetId = sessionId || activeSessionId.value
        if (!targetId || !sessions[targetId]) return
        setActiveTerminal(targetId)
        sshModalVisible.value = true
        setTimeout(() => { fitTerminal(); focusTerminal() }, 50)
    }

    function closeTerminalSession(sessionId) {
        const session = sessions[sessionId]
        if (!session) return
        try { if (session.socket) session.socket.close() } catch (e) { /* ignore */ }
        try {
            if (session.inputDisposable) session.inputDisposable.dispose()
            if (session.resizeDisposable) session.resizeDisposable.dispose()
            if (session.term) session.term.dispose()
        } catch (e) { /* ignore */ }
        if (session.container && session.container.parentNode) {
            session.container.parentNode.removeChild(session.container)
        }
        delete sessions[sessionId]
        sessionOrder.value = sessionOrder.value.filter(id => id !== sessionId)
        if (activeSessionId.value === sessionId) {
            const next = sessionOrder.value[0]
            if (next) {
                setActiveTerminal(next)
            } else {
                activeSessionId.value = null
                sshModalVisible.value = false
                setSshStatus(false, '未连接')
            }
        }
    }

    function closeSshModal(forceClose) {
        const session = getActiveTerminal()
        if (!session) {
            sshModalVisible.value = false
            return
        }
        if (!forceClose && isTerminalConnected(session)) {
            minimizeSshModal()
            toast('终端已最小化，连接保持中', 'info')
            return
        }
        closeTerminalSession(session.id)
    }

    function connectTerminal() {
        const session = getActiveTerminal()
        if (!session) return
        if (isTerminalConnected(session)) {
            toast('当前终端已经连接', 'info')
            return
        }
        if (!token.value) {
            toast('请先登录', 'error')
            return
        }
        const wsProto = location.protocol === 'https:' ? 'wss' : 'ws'
        const url = `${wsProto}://${location.host}/api/assets/${session.assetId}/terminal?token=${encodeURIComponent(token.value)}`
        session.socket = new WebSocket(url)
        session.statusText = '连接中...'
        setSshStatus(false, session.statusText)
        appendTerminal('\r\n[正在打开交互终端...]\r\n', 'info', session.id)

        session.socket.onopen = function () {
            session.connected = true
            session.statusText = '已连接'
            setSshStatus(true, '已连接')
            fitTerminal()
            focusTerminal()
        }
        session.socket.onmessage = function (event) {
            if (session.term) session.term.write(event.data)
        }
        session.socket.onerror = function () {
            session.statusText = '连接异常'
            appendTerminal('\r\n[WebSocket 连接异常]\r\n', 'err', session.id)
            if (activeSessionId.value === session.id) setSshStatus(false, '连接异常')
        }
        session.socket.onclose = function () {
            session.connected = false
            session.statusText = '未连接'
            appendTerminal('\r\n[终端连接已关闭]\r\n', 'info', session.id)
            session.socket = null
            if (activeSessionId.value === session.id) setSshStatus(false, '未连接')
        }
    }

    function disconnectTerminal() {
        const session = getActiveTerminal()
        if (session && session.socket) {
            session.socket.close()
            session.socket = null
        }
        if (session) session.statusText = '未连接'
        setSshStatus(false, '未连接')
    }

    function sendCtrlC() {
        sendTerminalData('\x03')
        focusTerminal()
    }

    function sendTerminalData(data, silent = false, sessionId) {
        const session = sessionId ? sessions[sessionId] : getActiveTerminal()
        if (!isTerminalConnected(session)) {
            if (!silent) toast('请先点击“连接终端”', 'error')
            return false
        }
        session.socket.send(data)
        return true
    }

    function clearTerminal() {
        const session = getActiveTerminal()
        if (session && session.term) session.term.clear()
    }

    async function testSsh() {
        const session = getActiveTerminal()
        if (!session) return
        const { api } = useApi()
        appendTerminal('\r\n[测试连接中...]\r\n', 'cmd', session.id)
        try {
            const res = await api('/assets/' + session.assetId + '/test-connection', {
                method: 'POST',
                body: JSON.stringify({ timeout: 10 })
            })
            if (res.success) {
                appendTerminal(`[连接成功] ${res.hostname} (${res.ip}) - ${res.message}\r\n`, 'info', session.id)
            } else {
                appendTerminal(`[连接失败] ${res.message}\r\n`, 'err', session.id)
            }
        } catch (e) {
            appendTerminal(`[错误] ${e.message}\r\n`, 'err', session.id)
        }
    }

    async function uploadRemoteFile(file, remoteDir, remoteName) {
        const assetId = currentSshAssetId.value
        if (!assetId) return
        if (!file) { toast('请选择要上传的文件', 'error'); return }
        if (!remoteDir) { toast('请填写上传目录', 'error'); return }
        try {
            const finalName = remoteName || file.name
            const previewPath = remoteDir.replace(/\/+$/, '') + '/' + finalName
            appendTerminal(`\r\n[上传中] ${file.name} -> ${previewPath}\r\n`, 'info')
            let url = API + '/assets/' + assetId + '/upload?remote_dir=' + encodeURIComponent(remoteDir)
            if (remoteName) url += '&remote_name=' + encodeURIComponent(remoteName)
            const res = await rawFetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/octet-stream',
                    'X-Filename': encodeURIComponent(file.name)
                },
                body: file
            })
            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.detail || '上传失败')
            }
            const data = await res.json()
            appendTerminal(`[上传成功] ${data.remote_path}\r\n`, 'info')
            toast('上传成功', 'success')
        } catch (e) {
            appendTerminal(`[上传失败] ${e.message}\r\n`, 'err')
            toast('上传失败: ' + e.message, 'error')
        }
    }

    async function downloadRemoteFile(remotePath) {
        const assetId = currentSshAssetId.value
        if (!assetId) return
        if (!remotePath) { toast('请填写远程文件路径', 'error'); return }
        try {
            appendTerminal(`\n[下载中] ${remotePath}\n`, 'info')
            const res = await rawFetch(API + '/assets/' + assetId + '/download?remote_path=' + encodeURIComponent(remotePath))
            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.detail || '下载失败')
            }
            const blob = await res.blob()
            const filename = remotePath.split('/').filter(Boolean).pop() || 'download.bin'
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = filename
            document.body.appendChild(a)
            a.click()
            a.remove()
            URL.revokeObjectURL(url)
            appendTerminal(`[下载已开始] ${filename}\n`, 'info')
            toast('下载已开始', 'success')
        } catch (e) {
            appendTerminal(`[下载失败] ${e.message}\n`, 'err')
            toast('下载失败: ' + e.message, 'error')
        }
    }

    function disposeAll() {
        Object.keys(sessions).forEach(id => {
            const s = sessions[id]
            try { if (s.socket) s.socket.close() } catch (e) { /* ignore */ }
            try { if (s.term) s.term.dispose() } catch (e) { /* ignore */ }
        })
        Object.keys(sessions).forEach(id => delete sessions[id])
        sessionOrder.value = []
        activeSessionId.value = null
        sshModalVisible.value = false
    }

    return {
        sessions,
        sessionOrder,
        activeSessionId,
        activeSession,
        currentSshAssetId,
        sshModalVisible,
        floatSessions,
        isTerminalConnected,
        getActiveTerminal,
        appendTerminal,
        setSshStatus,
        fitTerminal,
        focusTerminal,
        setActiveTerminal,
        createTerminalSession,
        openSshModal,
        minimizeSshModal,
        restoreSshModal,
        closeTerminalSession,
        closeSshModal,
        connectTerminal,
        disconnectTerminal,
        sendCtrlC,
        sendTerminalData,
        clearTerminal,
        testSsh,
        uploadRemoteFile,
        downloadRemoteFile,
        disposeAll
    }
}
