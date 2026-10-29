import { createServer } from 'vite'

// Own the Vite lifecycle directly: Windows hosts may not expose taskkill on PATH.
// Reuse a user's existing dev server locally, but never close a server we did not start.
export default async function setup() {
  if (!process.env.CI) {
    try {
      const response = await fetch('http://127.0.0.1:5173', { signal: AbortSignal.timeout(1500) })
      if (response.ok) return
    } catch {
      /* No existing dev server. */
    }
  }
  const server = await createServer({
    server: { host: '127.0.0.1', port: 5173, strictPort: true },
  })
  try {
    await server.listen()
  } catch (error) {
    await server.close()
    throw error
  }
  return async () => {
    await server.close()
  }
}
