// The minimal look: the three lists and nothing else, one column, for a phone or a panel next
// to the terminal. Projects and bots are reached through the side menu. No collector half.
(() => {
  const F = window.fleet;
  const {esc, todo, actions, when} = F;
  F.set("minimal", {
    title: "minimal",
    css: `.grid { grid-template-columns: 1fr; } main { max-width: 640px; }`,
    overview(d) {
      const items = actions(d);
      return todo(items) || `<div class="card"><h3>Nothing waits on you</h3><div class="dim">as of ${esc(when(d.generated))}</div></div>`;
    },
  });
})();
