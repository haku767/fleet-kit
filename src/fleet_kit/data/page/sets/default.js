// The default look: a plain desk. The three lists (merge, decide, step in), one card per
// project with its pipeline bar, its ready issues and open pull requests in numbers and its
// bots as a line each with a state dot; the guards and the machine as a footer line. The
// project, bot and machine pages are the frame's plain ones. No collector half.
(() => {
  const F = window.fleet;
  const {esc, tag, todo, actions, pipelineBar, projectChips, projectBots, botLine, slimLine, plink, queued, guardsLine} = F;
  function projectCard(d, p, items) {
    const bots = projectBots(d, p), mine = items.filter(i => i.project === p.name), merges = mine.filter(i => i.kind === "merge").length;
    const chips = projectChips(d, p, items);
    if (!bots.length && merges) chips.unshift(tag(`${merges} merge`, "green"));
    const counts = bots.length ? `<div class="slim"><b>${queued(p).length}</b> ready · <b>${p.prs.filter(pr => !pr.draft).length}</b> open pull request${p.prs.length === 1 ? "" : "s"}</div>` : "";
    const body = bots.length ? `${pipelineBar(d, p, items)}${counts}${bots.map(botLine).join("")}` : slimLine(d, p);
    return `<div class="card proj"><h3><a href="${plink(p.name)}">${esc(p.name)}</a><span class="chips">${chips.join("")}</span></h3>${body}</div>`;
  }
  F.set("default", {
    title: "default",
    overview(d) {
      const items = actions(d);
      return `${todo(items) || `<div class="card"><h3>Nothing waits on you</h3><div class="dim">the lists are empty; the bots are at work or asleep</div></div>`}
        <h2>Projects</h2>
        <div class="grid">${d.projects.map(p => projectCard(d, p, items)).join("")}</div>
        <div class="foot"><span>guards: <a href="#machine">${guardsLine(d)}</a></span><span><a href="#machine">${esc(d.machine.name)}</a></span></div>`;
    },
  });
})();
