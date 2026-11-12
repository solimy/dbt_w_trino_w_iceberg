export const sessionEvents = new EventTarget()

export async function fetchTrino(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const response = await fetch(input, { ...init, credentials: 'same-origin' })
    if (response.status === 401) {
        sessionEvents.dispatchEvent(new Event('expired'))
    }
    return response
}
