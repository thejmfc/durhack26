// Global exploration is the default; existing high-resolution site workflows retain their URL.
if (new URLSearchParams(location.search).has('site')) {
  import('./main');
} else {
  import('./moon-app');
}
export {};
