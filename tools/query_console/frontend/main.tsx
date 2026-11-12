import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { loader } from '@monaco-editor/react'
import { QueryEditor } from '../src/index'
import * as monaco from '../example/monaco'
import { fetchTrino, sessionEvents } from './session'
import './styles.css'

loader.config({ monaco })

function App() {
    const editorContainer = useRef<HTMLDivElement>(null)
    const [editorHeight, setEditorHeight] = useState(0)
    const [user, setUser] = useState<string | null>(null)
    const [sessionExpired, setSessionExpired] = useState(false)
    const [sessionError, setSessionError] = useState(false)
    const [signOutError, setSignOutError] = useState(false)

    useLayoutEffect(() => {
        const container = editorContainer.current
        if (!container) return

        const observer = new ResizeObserver(([entry]) => setEditorHeight(entry.contentRect.height))
        observer.observe(container)
        return () => observer.disconnect()
    }, [])

    useEffect(() => {
        const controller = new AbortController()
        const { signal } = controller
        sessionEvents.addEventListener('expired', () => setSessionExpired(true), { signal })
        fetchTrino('/api/session', { signal })
            .then(async (response) => {
                if (response.status === 401) return
                if (!response.ok) throw new Error('Failed to load session')
                const session = await response.json()
                setUser(session.user)
            })
            .catch(() => {
                if (!signal.aborted) setSessionError(true)
            })
        return () => controller.abort()
    }, [])

    async function signOut(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        setSignOutError(false)
        try {
            const response = await fetch('/logout', { method: 'POST' })
            if (!response.ok) throw new Error('Sign-out failed')
            window.location.assign('/logged-out')
        } catch {
            setSignOutError(true)
        }
    }

    return (
        <div className="console">
            <header className="console-header">
                <h1>Trino Query Console</h1>
                {user && (
                    <form action="/logout" method="post" onSubmit={signOut} className="account">
                        <span>{user}</span>
                        <button type="submit">Sign out</button>
                    </form>
                )}
            </header>
            {sessionExpired && (
                <div className="notice" role="alert">
                    Your session expired. <a href="/login">Sign in again</a>
                </div>
            )}
            {sessionError && (
                <div className="notice" role="alert">
                    Could not load your session. <a href="/">Reload the console</a>
                </div>
            )}
            {signOutError && (
                <div className="notice" role="alert">
                    Could not sign out. Try again.
                </div>
            )}
            <div className="editor" ref={editorContainer}>
                {user && editorHeight > 0 && <QueryEditor theme="dark" height={editorHeight} />}
            </div>
        </div>
    )
}

createRoot(document.getElementById('root')!).render(<App />)
