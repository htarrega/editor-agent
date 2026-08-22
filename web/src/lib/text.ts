const numberFormat = new Intl.NumberFormat('es-ES');

export const formatNumber = (value: number) => numberFormat.format(value);

/**
 * Barato a propósito: corta en el primer carácter visible en vez de contar.
 * Lo llama cada tecla para habilitar los botones, con manuscritos de novela
 * entera detrás, así que no puede recorrer ni copiar la cadena.
 */
export function hasContent(text: string): boolean {
  return /\S/.test(text);
}

/** Los signos sueltos (" . ", " ... ") no son palabras. */
export function countWords(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).filter((token) => /[\p{L}\p{N}]/u.test(token))
    .length;
}

/** Los saltos de línea dobles separan párrafos; los simples no. */
export function toParagraphs(text: string): string[] {
  return text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

/** "1.234 palabras · 12 párrafos", con singulares correctos. */
export function describeText(text: string): string {
  const words = countWords(text);
  const paragraphs = toParagraphs(text).length;
  return [
    `${formatNumber(words)} ${words === 1 ? 'palabra' : 'palabras'}`,
    `${formatNumber(paragraphs)} ${paragraphs === 1 ? 'párrafo' : 'párrafos'}`,
  ].join(' · ');
}

/**
 * Entrega el texto como .txt al usuario. El enlace va al documento y la URL
 * sobrevive al click: revocarla en la misma vuelta del bucle de eventos
 * cancela la descarga en Firefox y Safari.
 */
export function downloadText(text: string, fileName: string): void {
  const blob = new Blob([text], {type: 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** "novela.docx" → "novela-corregido.txt" */
export function correctedFileName(sourceName: string | null): string {
  const base = (sourceName ?? 'manuscrito').replace(/\.[^.]+$/, '');
  return `${base}-corregido.txt`;
}
