import { useAuth } from './useAuth'

export function useApi() {
    const API = '/api'

    async function api(path, opts = {}) {
        const { token, logout } = useAuth()
        const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) }
        if (token.value) headers.Authorization = 'Bearer ' + token.value
        const res = await fetch(API + path, { ...opts, headers })
        if (res.status === 401) {
            logout()
            throw new Error('未登录或登录已过期')
        }
        if (!res.ok) {
            const e = await res.json().catch(() => ({}))
            throw new Error(e.detail || 'HTTP ' + res.status)
        }
        if (res.status === 204) return null
        return res.json()
    }

    // Raw fetch with auth header but no JSON body normalization. Used by file
    // upload/download endpoints that send binary data or expect a blob response.
    async function rawFetch(path, opts = {}) {
        const { token, logout } = useAuth()
        const headers = { ...(opts.headers || {}) }
        if (token.value) headers.Authorization = 'Bearer ' + token.value
        const res = await fetch(API + path, { ...opts, headers })
        if (res.status === 401) {
            logout()
            throw new Error('未登录或登录已过期')
        }
        return res
    }

    return { api, rawFetch, API }
}
