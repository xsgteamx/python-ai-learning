// Pure JS SHA-256 + RSA password encryption, migrated from original index.html.
// Does not depend on browser crypto.subtle for SHA-256 (works on HTTP), but RSA
// transport encryption requires a secure context (HTTPS or localhost).

import { useApi } from '../composables/useApi'

export function sha256Hex(text) {
    function rr(n, x) { return (x >>> n) | (x << (32 - n)); }
    function ch(x, y, z) { return (x & y) ^ (~x & z); }
    function maj(x, y, z) { return (x & y) ^ (x & z) ^ (y & z); }
    function s0(x) { return rr(2, x) ^ rr(13, x) ^ rr(22, x); }
    function s1(x) { return rr(6, x) ^ rr(11, x) ^ rr(25, x); }
    function g0(x) { return rr(7, x) ^ rr(18, x) ^ (x >>> 3); }
    function g1(x) { return rr(17, x) ^ rr(19, x) ^ (x >>> 10); }
    const K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
    let H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const bytes = [];
    for (let i = 0; i < text.length; i++) {
        const c = text.charCodeAt(i);
        if (c < 0x80) bytes.push(c);
        else if (c < 0x800) { bytes.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f)); }
        else if (c < 0xd800 || c >= 0xe000) { bytes.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f)); }
        else {
            const n = text.charCodeAt(++i);
            const cp = 0x10000 + (((c & 0x3ff) << 10) | (n & 0x3ff));
            bytes.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
        }
    }
    const bitLen = bytes.length * 8;
    bytes.push(0x80);
    while ((bytes.length * 8 + 64) % 512 !== 0) bytes.push(0);
    const bitLenHigh = Math.floor(bitLen / 0x100000000);
    const bitLenLow = bitLen >>> 0;
    bytes.push(
        (bitLenHigh >>> 24) & 0xff,
        (bitLenHigh >>> 16) & 0xff,
        (bitLenHigh >>> 8) & 0xff,
        bitLenHigh & 0xff,
        (bitLenLow >>> 24) & 0xff,
        (bitLenLow >>> 16) & 0xff,
        (bitLenLow >>> 8) & 0xff,
        bitLenLow & 0xff
    );
    for (let i = 0; i < bytes.length; i += 64) {
        const W = new Array(64);
        for (let t = 0; t < 16; t++) W[t] = (bytes[i + t * 4] << 24) | (bytes[i + t * 4 + 1] << 16) | (bytes[i + t * 4 + 2] << 8) | bytes[i + t * 4 + 3];
        for (let t = 16; t < 64; t++) W[t] = (g1(W[t - 2]) + W[t - 7] + g0(W[t - 15]) + W[t - 16]) >>> 0;
        let [a, b, c, d, e, f, g, h] = H;
        for (let t = 0; t < 64; t++) {
            const T1 = (h + s1(e) + ch(e, f, g) + K[t] + W[t]) >>> 0;
            const T2 = (s0(a) + maj(a, b, c)) >>> 0;
            h = g; g = f; f = e; e = (d + T1) >>> 0; d = c; c = b; b = a; a = (T1 + T2) >>> 0;
        }
        H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
        H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
    }
    return H.map(h => ('00000000' + h.toString(16)).slice(-8)).join('');
}

let transportPublicKey = null

function pemToArrayBuffer(pem) {
    const b64 = pem.replace(/-----BEGIN PUBLIC KEY-----|-----END PUBLIC KEY-----|\s/g, '')
    const binary = atob(b64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    return bytes.buffer
}

async function getTransportPublicKey() {
    const subtle = (window.crypto || {}).subtle
    if (!subtle) throw new Error('当前环境不支持密码加密，请使用 HTTPS 或 localhost 访问')
    if (transportPublicKey) return transportPublicKey
    const { api } = useApi()
    const data = await api('/auth/public-key')
    transportPublicKey = await subtle.importKey(
        'spki',
        pemToArrayBuffer(data.public_key),
        { name: 'RSA-OAEP', hash: 'SHA-256' },
        false,
        ['encrypt']
    )
    return transportPublicKey
}

export function transportCryptoAvailable() {
    return !!(window.crypto && window.crypto.subtle)
}

// Returns encrypted payload (base64 RSA ciphertext) when subtle crypto is available,
// otherwise returns the SHA-256 digest so login still works on plain HTTP.
export async function encryptPasswordForTransport(password) {
    const passwordDigest = sha256Hex(password)
    if (!transportCryptoAvailable()) {
        return passwordDigest
    }
    const publicKey = await getTransportPublicKey()
    const subtle = window.crypto.subtle
    const encrypted = await subtle.encrypt(
        { name: 'RSA-OAEP' },
        publicKey,
        new TextEncoder().encode(passwordDigest)
    )
    const bytes = new Uint8Array(encrypted)
    let binary = ''
    bytes.forEach(b => binary += String.fromCharCode(b))
    return btoa(binary)
}
