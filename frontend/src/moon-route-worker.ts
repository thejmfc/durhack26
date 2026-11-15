import { planRoute, RouteRequest } from './moon-route';

interface RouteMessage { request: RouteRequest; port: MessagePort }

globalThis.addEventListener('message', (event: MessageEvent<RouteMessage>) => {
  const { request, port } = event.data;
  try {
    port.postMessage({ plan: planRoute(request) });
  } catch (error) {
    port.postMessage({ error: error instanceof Error ? error.message : String(error) });
  } finally {
    port.close();
  }
}, { once: true });
