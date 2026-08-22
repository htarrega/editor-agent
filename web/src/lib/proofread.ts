/**
 * ─────────────────────────────────────────────────────────────────────────────
 *  PUNTO DE ENGANCHE DEL CORRECTOR
 *
 *  La firma no cambia —texto entra, texto corregido sale— pero por debajo ya
 *  no es una sola petición. Una corrección tarda del orden de un minuto, así
 *  que el backend acepta el trabajo (202) y se consulta hasta que termina.
 *  Un `fetch` bloqueante de esa duración se lo come el timeout de cualquier
 *  proxy, y desde el navegador es indistinguible de un servidor caído.
 *
 *  Contrato que la UI espera, intacto:
 *    · resuelve con el texto corregido completo
 *    · rechaza con un Error cuyo `message` se muestra al usuario
 *    · atiende a `signal` para poder cancelar
 * ─────────────────────────────────────────────────────────────────────────────
 */

const API = import.meta.env.VITE_API_URL ?? '/api';

/** Cada cuánto se pregunta. Corto al principio —los textos breves acaban
 *  rápido— y luego se espacia, para no hacer 60 peticiones a un trabajo que
 *  va a tardar un minuto. */
const POLL_MS = [500, 1000, 2000, 3000] as const;
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

type Job = {
  job_id: string;
  status: 'running' | 'completed' | 'failed';
  text: string | null;
  applied: number;
  proposed: number;
  skipped: number;
  errors: string[];
  detail: string | null;
};

export async function proofread(
  text: string,
  signal?: AbortSignal,
): Promise<string> {
  const job = await submit(text, signal);
  const done = await poll(job.job_id, signal);
  if (done.text === null) {
    throw new Error(done.detail ?? 'El corrector no ha podido terminar.');
  }
  return done.text;
}

async function submit(text: string, signal?: AbortSignal): Promise<Job> {
  const res = await fetch(`${API}/jobs`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text}),
    signal,
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

async function poll(jobId: string, signal?: AbortSignal): Promise<Job> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  for (let attempt = 0; Date.now() < deadline; attempt++) {
    await wait(POLL_MS[Math.min(attempt, POLL_MS.length - 1)], signal);
    const res = await fetch(`${API}/jobs/${jobId}`, {signal});
    if (!res.ok) throw new Error(await detail(res));
    const job: Job = await res.json();
    if (job.status !== 'running') return job;
  }
  throw new Error('El corrector ha tardado demasiado.');
}

/**
 * El backend explica sus rechazos en castellano y en el `detail` —el texto
 * está vacío, supera el límite de palabras—, así que se muestran tal cual en
 * vez de taparlos con un mensaje genérico.
 */
async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
  } catch {
    /* respuesta sin JSON: cae al mensaje de abajo */
  }
  return `El corrector no respondió (${res.status}).`;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Cancelado', 'AbortError'));
      return;
    }
    const id = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(id);
      reject(new DOMException('Cancelado', 'AbortError'));
    });
  });
}
