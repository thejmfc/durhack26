export function elevationDownloadEstimate(received: number, total: number, elapsedSeconds: number): string {
  if (!Number.isFinite(total) || total <= 0) return 'Download time estimate unavailable.';
  if (received >= total) return 'Download complete; preparing the Moon…';
  if (received <= 0 || elapsedSeconds < 1) return 'Estimating download time…';
  const seconds = Math.ceil((total - received) * elapsedSeconds / received);
  if (seconds < 60) return `About ${seconds} sec left to download.`;
  return `About ${Math.ceil(seconds / 60)} min left to download.`;
}
