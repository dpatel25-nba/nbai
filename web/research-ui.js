/* Read-only bootstrap; no localStorage, settlement endpoint or betting action. */
(function () {
  'use strict';
  const root = document.getElementById('researchBoard');
  if (!root || !window.NBAI_RESEARCH) return;
  window.NBAI_RESEARCH.loadBoard(root, {
    fetcher: window.fetch.bind(window),
    schedule: window.setInterval.bind(window),
    cancel: window.clearInterval.bind(window)
  });
})();
