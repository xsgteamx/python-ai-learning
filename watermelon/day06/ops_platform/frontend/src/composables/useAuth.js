import { ref, computed } from 'vue'
import { useApi } from './useApi'

const token = ref(localStorage.getItem('ops_token') || '')
const currentUser = ref(null)

export function useAuth() {
    const { api } = useApi()

    const isAuthenticated = computed(() => !!token.value)
    const isAdmin = computed(() => currentUser.value?.role === 'admin')

    async function login(username, password) {
        const { encryptPasswordForTransport } = await import('../utils/crypto')
        const encryptedPassword = await encryptPasswordForTransport(password)
        const data = await api('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ username, password: encryptedPassword })
        })
        token.value = data.token
        localStorage.setItem('ops_token', data.token)
        currentUser.value = data.user
        return data
    }

    function logout() {
        token.value = ''
        currentUser.value = null
        localStorage.removeItem('ops_token')
    }

    async function checkAuth() {
        if (!token.value) return false
        try {
            currentUser.value = await api('/auth/me')
            return true
        } catch {
            logout()
            return false
        }
    }

    return { token, currentUser, isAuthenticated, isAdmin, login, logout, checkAuth }
}
